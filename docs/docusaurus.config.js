// @ts-check

const {themes} = require('prism-react-renderer');
const lightCodeTheme = themes.github;
const darkCodeTheme = themes.dracula;

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
  favicon: 'img/banner.jpeg',
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
        defaultMode: 'light',
        disableSwitch: false,
        respectPrefersColorScheme: false,
      },
      navbar: {
        title: 'ProtoAgent',
        logo: {
          alt: 'ProtoAgent',
          src: 'img/banner.jpeg',
        },
        items: [
          {to: '/docs/intro', label: 'Docs', position: 'left'},
          {to: '/docs/cli/overview', label: 'CLI', position: 'left'},
          {to: '/docs/core/overview', label: 'Core', position: 'left'},
          {to: '/docs/acp/overview', label: 'ACP', position: 'left'},
          {
            href: 'https://github.com/nMaroulis/protoagent/blob/main/CHANGELOG.md',
            label: 'Changelog',
            position: 'right',
          },
          {
            href: 'https://github.com/nMaroulis/protoagent',
            label: 'GitHub',
            position: 'right',
          },
        ],
      },
      footer: {
        style: 'dark',
        links: [
          {
            title: 'Start',
            items: [
              {label: 'Install', to: '/docs/getting-started/installation'},
              {label: 'First Run', to: '/docs/getting-started/first-run'},
              {label: 'CLI Commands', to: '/docs/cli/commands'},
            ],
          },
          {
            title: 'Architecture',
            items: [
              {label: 'Runtime Flow', to: '/docs/core/runtime'},
              {label: 'Agent Deck', to: '/docs/core/agents'},
              {label: 'Context Loom', to: '/docs/core/context-loom'},
            ],
          },
          {
            title: 'Operate',
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
        copyright: `Copyright ${new Date().getFullYear()} ProtoAgent.`,
      },
      prism: {
        theme: lightCodeTheme,
        darkTheme: darkCodeTheme,
        additionalLanguages: ['bash', 'json', 'rust', 'python', 'toml'],
      },
      mermaid: {
        theme: {light: 'neutral', dark: 'dark'},
      },
    }),
};

module.exports = config;
