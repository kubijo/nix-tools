// Render using the actual Nunjucks compiler, including imports and inheritance.
const fs = require("node:fs");
const nunjucks = require(process.argv[2]);
const { templates, name, contexts } = JSON.parse(fs.readFileSync(0, "utf8"));
const Loader = nunjucks.Loader.extend({
  getSource(path) {
    if (!(path in templates)) return null;
    return { src: templates[path], path, noCache: true };
  },
});
const environment = new nunjucks.Environment(new Loader(), {
  autoescape: true,
  throwOnUndefined: true,
});
process.stdout.write(
  JSON.stringify(contexts.map((context) => environment.render(name, context))),
);
