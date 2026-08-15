import fs from 'fs';
import path from 'path';
import crypto from 'crypto';
import * as tar from 'tar';
import properLockfile from 'proper-lockfile';
import { collectSourceFiles } from './gitignore';
import { SnapshotHandle } from '../types';

interface SnapshotRecord extends SnapshotHandle {
    file_manifest: string[];
}

export class SnapshotStore {
    private projectRoot: string;
    private storeDir: string;
    private archiveDir: string;
    private indexPath: string;
    private lockDir: string;

    constructor(projectRoot: string, storeSubdir = '.ai_runtime') {
        this.projectRoot = path.resolve(projectRoot);
        this.storeDir = path.join(this.projectRoot, storeSubdir);
        this.archiveDir = path.join(this.storeDir, 'snapshots');
        this.indexPath = path.join(this.storeDir, 'snapshots.json');
        this.lockDir = this.storeDir; 

        fs.mkdirSync(this.archiveDir, { recursive: true });
        if (!fs.existsSync(this.indexPath)) {
            fs.writeFileSync(this.indexPath, '[]\n');
        }
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

    private readIndex(): SnapshotRecord[] {
        if (!fs.existsSync(this.indexPath)) {
            return [];
        }
        const raw = fs.readFileSync(this.indexPath, 'utf8').trim();
        if (!raw) {
            return [];
        }
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) {
            throw new Error(`Snapshot index is corrupt: ${this.indexPath}`);
        }
        return parsed as SnapshotRecord[];
    }

    private writeIndex(records: SnapshotRecord[]): void {
        const tmpPath = `${this.indexPath}.tmp`;
        fs.writeFileSync(tmpPath, `${JSON.stringify(records, null, 2)}\n`);
        fs.renameSync(tmpPath, this.indexPath);
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

            const records = this.readIndex();
            records.push({
                ...handle,
                file_manifest: relFiles
            });
            this.writeIndex(records);
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
            let manifestFiles: Set<string> = new Set();
            const records = this.readIndex();
            const record = records.find(r => r.snapshot_id === handle.snapshot_id);
            if (record?.file_manifest) {
                for (const f of record.file_manifest) {
                    // Normalise to absolute paths for comparison
                    manifestFiles.add(path.resolve(this.projectRoot, f));
                }
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

            for (const item of records) {
                if (item.snapshot_id === handle.snapshot_id) {
                    item.status = 'rolled_back';
                    break;
                }
            }
            this.writeIndex(records);
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
        const records = this.readIndex();
        for (const item of records) {
            if (item.snapshot_id === handle.snapshot_id) {
                item.status = 'committed';
                break;
            }
        }
        this.writeIndex(records);
        handle.status = 'committed';
    }

    public listSnapshots(limit = 50): SnapshotHandle[] {
        return this.readIndex()
            .sort((a, b) => b.created_at - a.created_at)
            .slice(0, limit)
            .map(({ file_manifest: _fileManifest, ...handle }) => handle);
    }

    public async prune(keep = 10): Promise<number> {
        return this.withLock(async () => {
            const records = this.readIndex();
            const pruneable = records
                .filter(item => item.status === 'committed' || item.status === 'rolled_back')
                .sort((a, b) => b.created_at - a.created_at);
            const toDelete = new Set(pruneable.slice(keep).map(item => item.snapshot_id));

            let deleted = 0;
            for (const item of records) {
                if (!toDelete.has(item.snapshot_id)) {
                    continue;
                }
                try {
                    if (fs.existsSync(item.path)) {
                        fs.unlinkSync(item.path);
                    }
                } catch (e) {}
                deleted++;
            }

            this.writeIndex(records.filter(item => !toDelete.has(item.snapshot_id)));
            return deleted;
        });
    }

    public load(snapshotId: string): SnapshotHandle | null {
        const row = this.readIndex().find(item => item.snapshot_id === snapshotId);
        if (!row) return null;
        const { file_manifest: _fileManifest, ...handle } = row;
        return handle;
    }
}
