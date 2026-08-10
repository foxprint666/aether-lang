import fs from 'fs';
import path from 'path';
import { SnapshotStore, collectSourceFiles } from '../src';

describe('SnapshotStore', () => {
    let store: SnapshotStore;
    const testDir = path.join(__dirname, 'test_snapshot_env');

    beforeEach(() => {
        if (fs.existsSync(testDir)) {
            fs.rmSync(testDir, { recursive: true, force: true });
        }
        fs.mkdirSync(testDir, { recursive: true });
        
        fs.writeFileSync(path.join(testDir, 'file1.txt'), 'content1');
        fs.writeFileSync(path.join(testDir, 'file2.txt'), 'content2');
        fs.mkdirSync(path.join(testDir, 'node_modules'));
        fs.writeFileSync(path.join(testDir, 'node_modules', 'ignored.txt'), 'ignore me');
        fs.writeFileSync(path.join(testDir, '.gitignore'), 'file2.txt\n');

        store = new SnapshotStore(testDir);
    });

    afterEach(() => {
        if (fs.existsSync(testDir)) {
            fs.rmSync(testDir, { recursive: true, force: true });
        }
    });

    test('collectSourceFiles respects gitignore and defaults', () => {
        const files = collectSourceFiles(testDir);
        const basenames = files.map(f => path.basename(f)).sort();
        expect(basenames).toContain('file1.txt');
        expect(basenames).toContain('.gitignore');
        expect(basenames).not.toContain('file2.txt');
        expect(basenames).not.toContain('ignored.txt');
    });

    test('capture and listSnapshots', async () => {
        const handle = await store.capture('patch-123');
        expect(handle.patch_id).toBe('patch-123');
        expect(handle.status).toBe('pending');
        expect(handle.file_count).toBeGreaterThan(0);
        expect(handle.archive_size_bytes).toBeGreaterThan(0);

        const list = store.listSnapshots();
        expect(list.length).toBe(1);
        expect(list[0].snapshot_id).toBe(handle.snapshot_id);
        expect(list[0].patch_id).toBe('patch-123');
    });

    test('restore rolled back changes', async () => {
        const handle = await store.capture('patch-456');
        
        // Modify a file
        const file1 = path.join(testDir, 'file1.txt');
        fs.writeFileSync(file1, 'modified content');

        // Restore
        await store.restore(handle);

        // Check if content reverted
        const content = fs.readFileSync(file1, 'utf8');
        expect(content).toBe('content1');

        expect(handle.status).toBe('rolled_back');
        const loaded = store.load(handle.snapshot_id);
        expect(loaded?.status).toBe('rolled_back');
    });

    test('commit updates status', async () => {
        const handle = await store.capture('patch-789');
        store.commit(handle);
        expect(handle.status).toBe('committed');
        
        const loaded = store.load(handle.snapshot_id);
        expect(loaded?.status).toBe('committed');
    });

    test('prune removes old snapshots', async () => {
        const h1 = await store.capture('p1');
        store.commit(h1);
        
        await new Promise(r => setTimeout(r, 10));

        const h2 = await store.capture('p2');
        store.commit(h2);

        await new Promise(r => setTimeout(r, 10));

        const h3 = await store.capture('p3');
        
        const deleted = await store.prune(1);
        expect(deleted).toBe(1); 
        
        const list = store.listSnapshots();
        expect(list.length).toBe(2);
        
        const snapIds = list.map(s => s.snapshot_id);
        expect(snapIds).toContain(h2.snapshot_id);
        expect(snapIds).toContain(h3.snapshot_id);
        expect(snapIds).not.toContain(h1.snapshot_id);
    });
});
