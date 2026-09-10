import React from 'react';
import CodeBlock from '@theme-original/CodeBlock';

// Keep Docusaurus highlighting, copy, line numbers, and MDX metadata intact.
export default function TerminalCodeBlock(props) {
  const language = props.language || props.className?.match(/language-([\w-]+)/)?.[1];
  const hasTitle = props.title !== undefined || /\btitle\s*=/.test(props.metastring || '');
  const title = hasTitle ? props.title : ({bash: 'shell', sh: 'shell', text: 'terminal'}[language] || language || 'code');
  return <CodeBlock {...props} title={title} />;
}
