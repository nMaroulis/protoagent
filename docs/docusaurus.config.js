// @ts-check

const {themes} = require('prism-react-renderer');
const lightCodeTheme = themes.github;
const darkCodeTheme = {
  plain: {color: '#eef3f8', backgroundColor: '#0e1018'},
  styles: [
    {types: ['comment', 'prolog', 'doctype', 'cdata'], style: {color: '#9aa7b8'}},
    {types: ['punctuation', 'operator'], style: {color: '#9aa7b8'}},
    {types: ['keyword', 'tag', 'atrule'], style: {color: '#e056d8'}},
    {types: ['string', 'char', 'attr-value', 'inserted'], style: {color: '#74df9f'}},
    {types: ['function', 'class-name', 'builtin'], style: {color: '#58dce9'}},
    {types: ['number', 'boolean', 'constant', 'symbol', 'property', 'attr-name'], style: {color: '#f3c65b'}},
    {types: ['deleted', 'important'], style: {color: '#ff7a8a'}},
  ],
};

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'ProtoAgent Docs',
  tagline: 'Local-first agent console, ProtoLink runtime, and monorepo guide.',
  url: 'https://nmaroulis.github.io',
  baseUrl: '/protoagent/',
  organizationName: 'nMaroulis',
  projectName: 'protoagent',
  trailingSlash: false,
  onBrokenLinks: 'throw',
  favicon: 'img/terminal.svg',
  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },
  markdown: {
    mermaid: true,
    hooks: {
      onBrokenMarkdownLinks: 'throw',
    },
  },
  themes: ['@docusaurus/theme-mermaid'],
  presets: [
    [
      'classic',
      {
        docs: {
          path: 'content',
          routeBasePath: 'docs',
          sidebarPath: require.resolve('./sidebars.js'),
          editUrl: 'https://github.com/nMaroulis/protoagent/edit/main/docs/',
          showLastUpdateAuthor: false,
          showLastUpdateTime: false,
        },
        blog: false,
        theme: {
          customCss: require.resolve('./src/css/custom.css'),
        },
      },
    ],
  ],
  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      image: 'img/banner.jpeg',
      colorMode: {
        defaultMode: 'dark',
        disableSwitch: false,
        respectPrefersColorScheme: false,
      },
      navbar: {
        title: 'protoagent_',
        logo: {
          alt: 'ProtoAgent',
          src: 'img/terminal.svg',
        },
        items: [
          {to: '/docs/intro', label: '/docs', position: 'left', activeBaseRegex: '/docs/(intro|getting-started|reference|contributing|playground)'},
          {to: '/docs/cli/overview', label: '/cli', position: 'left', activeBasePath: 'docs/cli'},
          {to: '/docs/core/overview', label: '/core', position: 'left', activeBasePath: 'docs/core'},
          {to: '/docs/acp/overview', label: '/acp', position: 'left', activeBasePath: 'docs/acp'},
          {
            href: 'https://github.com/nMaroulis/protoagent/blob/main/CHANGELOG.md',
            label: 'Changelog',
            position: 'right',
          },
          {
            href: 'https://github.com/nMaroulis/protoagent',
            label: 'GitHub',
            position: 'right',
            className: 'navbar-github',
          },
        ],
      },
      footer: {
        style: 'dark',
        links: [
          {
            title: 'start',
            items: [
              {label: 'Install', to: '/docs/getting-started/installation'},
              {label: 'First Run', to: '/docs/getting-started/first-run'},
              {label: 'CLI Commands', to: '/docs/cli/commands'},
            ],
          },
          {
            title: 'architecture',
            items: [
              {label: 'Runtime Flow', to: '/docs/core/runtime'},
              {label: 'Agent Deck', to: '/docs/core/agents'},
              {label: 'Context Loom', to: '/docs/core/context-loom'},
            ],
          },
          {
            title: 'operate',
            items: [
              {label: 'Environment', to: '/docs/reference/environment'},
              {label: 'Troubleshooting', to: '/docs/reference/troubleshooting'},
              {label: 'Maintenance', to: '/docs/contributing/maintenance'},
              {
                label: 'Changelog',
                href: 'https://github.com/nMaroulis/protoagent/blob/main/CHANGELOG.md',
              },
            ],
          },
        ],
        copyright: `protoagent_ / v0.2.3 / MIT · © ${new Date().getFullYear()} ProtoAgent`,
      },
      prism: {
        theme: lightCodeTheme,
        darkTheme: darkCodeTheme,
        additionalLanguages: ['bash', 'json', 'rust', 'python', 'toml'],
      },
      mermaid: {
        theme: {light: 'neutral', dark: 'dark'},
        options: {
          fontFamily: 'JetBrains Mono, monospace',
        },
      },
    }),
};

module.exports = config;
