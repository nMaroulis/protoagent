# ProtoAgent Docusaurus Docs

This folder contains the Docusaurus documentation site for the monorepo.

```bash
cd docs
npm install
npm run start
```

Content lives in `content/`. The Docusaurus app, theme, and static assets live
next to it so documentation updates can stay scoped to one page or category.

## GitHub Pages Deployment

The site is configured for GitHub Pages at `https://nmaroulis.github.io/protoagent/`.

Deployment is handled by `.github/workflows/docs.yml` whenever docs files are
pushed to `main`. The workflow installs from `docs/package-lock.json`, runs the
Docusaurus build, uploads `docs/build`, and publishes it through GitHub Pages.

In the repository settings, GitHub Pages should use **GitHub Actions** as the
publishing source.
