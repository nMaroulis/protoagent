# ProtoAgent Docusaurus Docs

This folder contains the Docusaurus documentation site for the monorepo.
Use Node.js 20 or newer.

```bash
cd docs
npm ci
npm run start
```

Content lives in `content/`. The Docusaurus app, theme, and static assets live
next to it so documentation updates can stay scoped to one page or category.

## Terminal Theme

The docs default to a dark terminal palette drawn from
`cli/src/terminal_ui/theme.rs`: near-black surfaces, cyan output, magenta
headers, amber code accents, and green status text. The theme switch also
offers a light palette. Existing saved theme preferences are respected.

- `src/css/custom.css` owns shared tokens, typography, navigation, article
  styles, code blocks, and responsive behavior.
- `src/pages/index.jsx` and `index.module.css` own the documentation home.
  Its terminal panel is an illustrative session; the command links open the
  corresponding documentation.
- `src/theme/CodeBlock/index.jsx` adds language labels while retaining
  Docusaurus highlighting, copying, line numbers, and authored titles.
- JetBrains Mono is bundled in `static/fonts/` under the SIL Open Font
  License in `static/fonts/OFL.txt`. Font loading makes no third-party
  request. Source: [JetBrains Mono](https://github.com/JetBrains/JetBrainsMono).
- The brief cursor animation stops after four cycles and respects reduced
  motion. Keep focus indicators visible and wide tables/code blocks scrollable.

Validate changes with `npm run build`. Useful preview routes are the home,
`docs/getting-started/installation`, `docs/intro`, and `docs/cli/commands`;
together they cover navigation, code, tables, and Mermaid diagrams.

## GitHub Pages Deployment

The site is configured for GitHub Pages at `https://nmaroulis.github.io/protoagent/`.

Deployment is handled by `.github/workflows/docs.yml` whenever docs files are
pushed to `main`. The workflow installs from `docs/package-lock.json`, runs the
Docusaurus build, uploads `docs/build`, and publishes it through GitHub Pages.

In the repository settings, GitHub Pages should use **GitHub Actions** as the
publishing source.
