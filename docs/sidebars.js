/** @type {import('@docusaurus/plugin-content-docs').SidebarsConfig} */
const sidebars = {
  docsSidebar: [
    'intro',
    {
      type: 'category',
      label: 'Getting Started',
      collapsed: false,
      items: [
        'getting-started/installation',
        'getting-started/first-run',
        'getting-started/provider-setup',
      ],
    },
    {
      type: 'category',
      label: 'CLI',
      collapsed: false,
      items: [
        'cli/overview',
        'cli/commands',
        'cli/tui',
        'cli/projects-and-sessions',
        'cli/models-and-config',
        'cli/context-loom',
        'cli/safety-tracing',
      ],
    },
    {
      type: 'category',
      label: 'Core',
      collapsed: false,
      items: [
        'core/overview',
        'core/architecture',
        'core/agents',
        'core/runtime',
        'core/quality-evals',
        'core/context-loom',
        'core/state-memory',
        'core/config-models',
        'core/safety-tools',
      ],
    },
    {
      type: 'category',
      label: 'ACP',
      collapsed: false,
      items: ['acp/overview', 'acp/plan'],
    },
    {
      type: 'category',
      label: 'Playground',
      collapsed: true,
      items: ['playground/overview'],
    },
    {
      type: 'category',
      label: 'Reference',
      collapsed: false,
      items: [
        'reference/file-map',
        'reference/versioning',
        'reference/environment',
        'reference/troubleshooting',
        'reference/verification',
      ],
    },
    {
      type: 'category',
      label: 'Contributing',
      collapsed: true,
      items: ['contributing/development-workflow', 'contributing/maintenance'],
    },
  ],
};

module.exports = sidebars;
