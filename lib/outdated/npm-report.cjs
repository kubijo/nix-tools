// Use the libraries shipped with the pinned npm, including its configuration loader.
const { createRequire } = require('node:module');
const path = require('node:path');
const npmRequire = createRequire(
    process.argv[3] === 'npm' ? require('node:fs').realpathSync(process.argv[4]) : process.argv[2],
);
const semver = npmRequire('semver');

function state(current, latest) {
    const order = semver.compare(current, latest);
    return order < 0 ? 'outdated' : order > 0 ? 'ahead' : 'up-to-date';
}

async function main() {
    if (process.argv[3] === 'compare') {
        const [current, latest] = process.argv.slice(4);
        if (!semver.valid(current) || !semver.valid(latest))
            return [
                { name: 'version', current, latest, state: 'skipped', detail: 'Release version is not valid SemVer' },
            ];
        return [{ name: 'version', current, latest, state: state(current, latest) }];
    }
    if (process.argv[3] === 'versions') {
        const rows = JSON.parse(require('node:fs').readFileSync(process.argv[4], 'utf8'));
        const npa = npmRequire('npm-package-arg');
        return rows.map(({ spec, ...row }) => {
            let registry;
            try {
                registry = npa.resolve(row.name, spec || row.current).registry;
            } catch {
                registry = false;
            }
            if (!registry)
                return { ...row, state: 'unknown', detail: 'Non-registry dependency needs a release policy' };
            if (row.latest) return { ...row, state: state(row.current, row.latest) };
            return { ...row, state: 'up-to-date', detail: 'Native report found no registry update' };
        });
    }

    const Arborist = npmRequire('@npmcli/arborist');
    const Config = npmRequire('@npmcli/config');
    const { definitions, shorthands, flatten } = npmRequire('@npmcli/config/lib/definitions');
    const pacote = npmRequire('pacote');
    const config = new Config({
        npmPath: path.dirname(npmRequire.resolve('npm/package.json')),
        definitions,
        shorthands,
        flatten,
        argv: [],
    });
    await config.load();
    config.validate();
    const options = { ...config.flat, path: process.cwd(), preferOnline: true, ignoreScripts: true };
    const tree = await new Arborist(options).loadVirtual();
    const cache = new Map();
    const rows = [];
    for (const node of tree.inventory.values()) {
        for (const edge of node.edgesOut.values()) {
            if (edge.error && !edge.optional) throw new Error('Invalid locked dependency graph');
        }
        if (node.isRoot) continue;
        const row = {
            name: node.packageName,
            source: `package-lock.json:${node.location}`,
            current: node.version || '',
        };
        if (node.isWorkspace || node.isLink) {
            rows.push({ ...row, state: 'skipped', detail: 'Local workspace/link dependency' });
        } else if (!node.isRegistryDependency) {
            rows.push({ ...row, state: 'unknown', detail: 'Non-registry dependency needs a release policy' });
        } else {
            if (!cache.has(node.packageName)) {
                cache.set(node.packageName, await pacote.packument(node.packageName, options));
            }
            const metadata = cache.get(node.packageName);
            const original = metadata.versions[node.version];
            if (!original || original.dist?.tarball !== node.resolved) {
                rows.push({
                    ...row,
                    state: 'unknown',
                    detail: 'Locked tarball does not match the configured registry',
                });
                continue;
            }
            const latest = metadata['dist-tags'].latest;
            rows.push({
                ...row,
                latest,
                state: state(node.version, latest),
                detail: 'Registry latest; no compatibility claim',
            });
        }
    }
    return rows;
}

main()
    .then(rows => process.stdout.write(JSON.stringify({ schemaVersion: 1, results: rows })))
    .catch(() => {
        // Native exceptions may contain registry credentials.
        process.stderr.write('Native npm report failed\n');
        process.exitCode = 2;
    });
