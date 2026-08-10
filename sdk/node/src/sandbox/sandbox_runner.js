/**
 * ai_runtime sandbox_runner
 * Worker script executed INSIDE the subprocess sandbox.
 * Receives the payload via stdin (JSON), executes it,
 * captures stdout/stderr, and writes a result JSON to a result file.
 */

const fs = require('fs');
const vm = require('vm');

async function main() {
    // Read the payload JSON from stdin
    const inputChunks = [];
    for await (const chunk of process.stdin) {
        inputChunks.push(chunk);
    }
    const input = Buffer.concat(inputChunks).toString('utf-8');

    let request;
    try {
        request = JSON.parse(input);
    } catch (e) {
        process.exit(2);
    }

    const { payload, result_path } = request;

    let exit_code = 0;
    let error_msg = null;

    let captured_out = '';
    let captured_err = '';

    const originalStdoutWrite = process.stdout.write.bind(process.stdout);
    const originalStderrWrite = process.stderr.write.bind(process.stderr);

    process.stdout.write = (chunk, encoding, callback) => {
        captured_out += chunk.toString();
        if (typeof encoding === 'function') encoding();
        else if (callback) callback();
        return true;
    };

    process.stderr.write = (chunk, encoding, callback) => {
        captured_err += chunk.toString();
        originalStderrWrite(chunk, encoding, callback); // also echo to real stderr so parent can catch ERR_ACCESS_DENIED
        return true;
    };

    try {
        const script = new vm.Script(payload, { filename: '<ai_patch>' });
        const context = vm.createContext({
            ...global,
            console: new console.Console(process.stdout, process.stderr),
            require: require,
            process: process,
            Buffer: Buffer,
            setTimeout: setTimeout,
            clearTimeout: clearTimeout,
            setInterval: setInterval,
            clearInterval: clearInterval,
            setImmediate: setImmediate,
            clearImmediate: clearImmediate,
            URL: URL,
            URLSearchParams: URLSearchParams
        });

        const result = script.runInContext(context, { timeout: 30000 });
        if (result instanceof Promise) {
            await result;
        }
    } catch (e) {
        exit_code = 1;
        error_msg = e.stack || String(e);
        captured_err += error_msg + '\n';
        originalStderrWrite(error_msg + '\n');
    } finally {
        process.stdout.write = originalStdoutWrite;
        process.stderr.write = originalStderrWrite;
    }

    const result_obj = {
        exit_code: exit_code,
        stdout: captured_out,
        stderr: captured_err,
        error: error_msg,
    };

    try {
        fs.writeFileSync(result_path, JSON.stringify(result_obj), 'utf-8');
    } catch (e) {
        // ignore
    }

    process.exit(exit_code);
}

main().catch(() => process.exit(1));
