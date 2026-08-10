export interface SnapshotHandle {
    snapshot_id: string;
    project_root: string;
    patch_id: string;
    path: string;
    status: string;
    created_at: number;
    archive_size_bytes: number;
    file_count: number;
}

export interface ExecutionResult {
    failed: boolean;
    exit_code: number;
    stdout: string;
    stderr: string;
    elapsed_ms: number;
    tier: string;
    error?: string;
}
