// Loaded into a disposable project by Yarn's supported plugin mechanism.
module.exports = {
    name: 'nix-tools-outdated',
    factory(require) {
        const { BaseCommand } = require('@yarnpkg/cli');
        const { Configuration, Project, structUtils, semverUtils } = require('@yarnpkg/core');
        const { npmHttpUtils } = require('@yarnpkg/plugin-npm');
        class OutdatedCommand extends BaseCommand {
            static paths = [['nix-tools-outdated']];
            async execute() {
                const configuration = await Configuration.find(this.context.cwd, this.context.plugins);
                const { project } = await Project.find(configuration, this.context.cwd);
                const manager = project.topLevelWorkspace.manifest.raw.packageManager;
                if (manager && !/^yarn@(?:[2-9]|[1-9][0-9]+)\./.test(manager)) {
                    throw new Error('Expected a modern Yarn project');
                }
                if (project.lockfileLastVersion === null || project.lockfileLastVersion < 0) {
                    throw new Error('Yarn Classic is unsupported');
                }
                if (!project.originalPackages.size) throw new Error('Empty Yarn inventory');
                const rows = [];
                const metadata = new Map();
                for (const pkg of project.originalPackages.values()) {
                    const locator = structUtils.isVirtualLocator(pkg) ? structUtils.devirtualizeLocator(pkg) : pkg;
                    const row = { name: structUtils.stringifyLocator(pkg), current: pkg.version || '' };
                    const range = structUtils.parseRange(locator.reference);
                    if (range.protocol === 'workspace:') {
                        rows.push({ ...row, state: 'skipped', detail: 'Local workspace package' });
                        continue;
                    }
                    if (range.protocol !== 'npm:') {
                        rows.push({
                            ...row,
                            state: 'unknown',
                            detail: 'Unsupported locator protocol; supply a release entry',
                        });
                        continue;
                    }
                    const ident = structUtils.stringifyIdent(locator);
                    if (!metadata.has(ident)) {
                        metadata.set(
                            ident,
                            await npmHttpUtils.get(npmHttpUtils.getIdentUrl(locator), {
                                configuration,
                                ident: locator,
                                jsonResponse: true,
                            }),
                        );
                    }
                    const document = metadata.get(ident);
                    const latest = document['dist-tags'].latest;
                    const order = new semverUtils.SemVer(pkg.version).compare(new semverUtils.SemVer(latest));
                    const ranges = [];
                    for (const descriptor of project.storedDescriptors.values()) {
                        if (project.storedResolutions.get(descriptor.descriptorHash) !== pkg.locatorHash) continue;
                        const parsed = structUtils.parseRange(descriptor.range);
                        if (parsed.protocol === 'npm:') {
                            const alias = structUtils.tryParseDescriptor(parsed.selector, true);
                            ranges.push(semverUtils.validRange(alias ? alias.range : parsed.selector));
                        }
                    }
                    const candidates = Object.keys(document.versions)
                        .map(v => new semverUtils.SemVer(v))
                        .filter(v => !v.prerelease.length && ranges.length && ranges.every(r => r?.test(v)))
                        .sort((a, b) => a.compare(b));
                    rows.push({
                        ...row,
                        latest,
                        compatible: candidates.at(-1)?.version || '',
                        state: order < 0 ? 'outdated' : order > 0 ? 'ahead' : 'up-to-date',
                        detail: 'Range-compatible availability is not a full peer/platform resolution',
                    });
                }
                this.context.stdout.write(JSON.stringify({ schemaVersion: 1, results: rows }));
            }
        }
        return { commands: [OutdatedCommand] };
    },
};
