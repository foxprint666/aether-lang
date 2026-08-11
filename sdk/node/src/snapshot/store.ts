import fs from 'fs';
import path from 'path';
import crypto from 'crypto';
import * as tar from 'tar';
import Database from 'better-sqlite3';
import properLockfile from 'proper-lockfile';
import { collectSourceFiles } from './gitignore';
import { SnapshotHandle } from '../types';

const DDL = `
CREATE TABLE IF NOT EXISTS snapshots (
    id                  TEXT PRIMARY KEY,
    patch_id            TEXT NOT NULL,
    project_root        TEXT NOT NULL,
    archive_path        TEXT NOT NULL,
    created_at          REAL NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    archive_size_bytes  INTEGER NOT NULL DEFAULT 0,
    file_count          INTEGER NOT NULL DEFAULT 0,
    file_manifest       TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_snapshots_patch_id
    ON snapshots (patch_id);

CREATE INDEX IF NOT EXISTS idx_snapshots_created_at
    ON snapshots (created_at DESC);
`;

export class SnapshotStore {
    private projectRoot: string;
    private storeDir: string;
    private archiveDir: string;
    private dbPath: string;
    private lockDir: string;

    constructor(projectRoot: string, storeSubdir = '.ai_runtime') {
        this.projectRoot = path.resolve(projectRoot);
        this.storeDir = path.join(this.projectRoot, storeSubdir);
        this.archiveDir = path.join(this.storeDir, 'snapshots');
        this.dbPath = path.join(this.storeDir, 'snapshots.db');
        this.lockDir = this.storeDir; 

        fs.mkdirSync(this.archiveDir, { recursive: true });
        this.initDb();
    }

    private initDb() {
        const db = this.connect();
        try {
            db.exec(DDL);
        } finally {
            db.close();
        }
    }

    private connect(): Database.Database {
        const db = new Database(this.dbPath, { timeout: 15000 });
        db.pragma('journal_mode = WAL');
        db.pragma('foreign_keys = ON');
        db.pragma('synchronous = NORMAL');
        return db;
    }

    private async withLock<T>(fn: () => Promise<T>): Promise<T> {
        fs.mkdirSync(this.lockDir, { recursive: true });
        let release: () => Promise<void>;
        try {
            release = await properLockfile.lock(this.lockDir, {
                lockfilePath: path.join(this.lockDir, 'snapshot.lock.dir'),
                retries: {
                    retries: 200, // 10s total wait
                    minTimeout: 50,
                    maxTimeout: 50
                }
            });
        } catch (e: any) {
            throw new Error(`Could not acquire snapshot lock within 10s: ${e.message}`);
        }

        try {
            return await fn();
        } finally {
            await release();
        }
    }

    public async capture(patchId = ''): Promise<SnapshotHandle> {
        const snapId = crypto.randomUUID();
        const archivePath = path.join(this.archiveDir, `${snapId}.tar.gz`);

        const handle: SnapshotHandle = {
            snapshot_id: snapId,
            project_root: this.projectRoot,
            patch_id: patchId,
            path: archivePath,
            status: 'pending',
            created_at: Date.now() / 1000,
            archive_size_bytes: 0,
            file_count: 0
        };

        await this.withLock(async () => {
            const { fileCount, sizeBytes, relFiles } = await this.writeArchive(archivePath);
            handle.file_count = fileCount;
            handle.archive_size_bytes = sizeBytes;

            const db = this.connect();
            try {
                db.prepare(`
                    INSERT INTO snapshots 
                    (id, patch_id, project_root, archive_path, created_at, status, archive_size_bytes, file_count, file_manifest)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                `).run(
                    snapId, patchId, this.projectRoot, archivePath, handle.created_at,
                    'pending', sizeBytes, fileCount, JSON.stringify(relFiles)
                );
            } finally {
                db.close();
            }
        });

        return handle;
    }

    private async writeArchive(dest: string): Promise<{ fileCount: number, sizeBytes: number, relFiles: string[] }> {
        const tmpPath = `${dest}.tmp`;
        const files = collectSourceFiles(this.projectRoot);
        const relFiles = files.map(f => path.relative(this.projectRoot, f));
        
        try {
            if (relFiles.length === 0) {
                await tar.c({
                    gzip: { level: 6 },
                    file: tmpPath,
                    cwd: this.projectRoot,
                    filter: () => false
                }, ['.']);
            } else {
                await tar.c({
                    gzip: { level: 6 },
                    file: tmpPath,
                    cwd: this.projectRoot,
                }, relFiles);
            }

            fs.renameSync(tmpPath, dest);
        } catch (e) {
            if (fs.existsSync(tmpPath)) {
                fs.unlinkSync(tmpPath);
            }
            throw e;
        }

        const sizeBytes = fs.existsSync(dest) ? fs.statSync(dest).size : 0;
        return { fileCount: files.length, sizeBytes, relFiles };
    }

    public async restore(handle: SnapshotHandle): Promise<void> {
        if (!handle.path || !fs.existsSync(handle.path)) {
            throw new Error(`Snapshot archive not found: '${handle.path}'. It may have been pruned or the snapshot was never fully committed.`);
        }

        await this.withLock(async () => {
            // Load the file manifest so we know what was in the snapshot
            const db = this.connect();
            let manifestFiles: Set<string> = new Set();
            try {
                const row = db.prepare(
                    `SELECT file_manifest FROM snapshots WHERE id=?`
                ).get(handle.snapshot_id) as any;
                if (row?.file_manifest) {
                    const parsed: string[] = JSON.parse(row.file_manifest);
                    for (const f of parsed) {
                        // Normalise to absolute paths for comparison
                        manifestFiles.add(path.resolve(this.projectRoot, f));
                    }
                }
            } finally {
                db.close();
            }

            // Extract the archive (restores original file contents)
            await this.extractArchive(handle.path);

            // Delete files that exist NOW but were NOT in the snapshot
            if (manifestFiles.size > 0) {
                const currentFiles = collectSourceFiles(this.projectRoot);
                for (const absFile of currentFiles) {
                    if (!manifestFiles.has(absFile)) {
                        try { fs.unlinkSync(absFile); } catch { /* best-effort */ }
                    }
                }
            }

            const db2 = this.connect();
            try {
                db2.prepare(`UPDATE snapshots SET status=? WHERE id=?`).run('rolled_back', handle.snapshot_id);
            } finally {
                db2.close();
            }
        });

        handle.status = 'rolled_back';
    }

    private async extractArchive(archivePath: string): Promise<void> {
        await tar.x({
            file: archivePath,
            cwd: this.projectRoot
        });
    }

    public commit(handle: SnapshotHandle): void {
        const db = this.connect();
        try {
            db.prepare(`UPDATE snapshots SET status=? WHERE id=?`).run('committed', handle.snapshot_id);
        } finally {
            db.close();
        }
        handle.status = 'committed';
    }

    public listSnapshots(limit = 50): SnapshotHandle[] {
        const db = this.connect();
        try {
            const rows = db.prepare(`
                SELECT id, patch_id, status, created_at, archive_size_bytes, file_count, archive_path
                FROM snapshots ORDER BY created_at DESC LIMIT ?
            `).all(limit) as any[];

            return rows.map(r => ({
                snapshot_id: r.id,
                patch_id: r.patch_id,
                project_root: this.projectRoot,
                path: r.archive_path,
                status: r.status,
                created_at: r.created_at,
                archive_size_bytes: r.archive_size_bytes,
                file_count: r.file_count
            }));
        } finally {
            db.close();
        }
    }

    public async prune(keep = 10): Promise<number> {
        return this.withLock(async () => {
            const db = this.connect();
            try {
                const rows = db.prepare(`
                    SELECT id, archive_path FROM snapshots 
                    WHERE status IN ('committed', 'rolled_back') 
                    ORDER BY created_at DESC LIMIT -1 OFFSET ?
                `).all(keep) as any[];

                let deleted = 0;
                for (const row of rows) {
                    try {
                        if (fs.existsSync(row.archive_path)) {
                            fs.unlinkSync(row.archive_path);
                        }
                    } catch (e) {}

                    db.prepare(`DELETE FROM snapshots WHERE id=?`).run(row.id);
                    deleted++;
                }
                return deleted;
            } finally {
                db.close();
            }
        });
    }

    public load(snapshotId: string): SnapshotHandle | null {
        const db = this.connect();
        try {
            const row = db.prepare(`
                SELECT id, patch_id, project_root, archive_path, created_at, status, archive_size_bytes, file_count
                FROM snapshots WHERE id = ?
            `).get(snapshotId) as any;

            if (!row) return null;

            return {
                snapshot_id: row.id,
                patch_id: row.patch_id,
                project_root: row.project_root,
                path: row.archive_path,
                status: row.status,
                created_at: row.created_at,
                archive_size_bytes: row.archive_size_bytes,
                file_count: row.file_count
            };
        } finally {
            db.close();
        }
    }
}
