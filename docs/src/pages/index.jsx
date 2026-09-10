import Link from '@docusaurus/Link';
import Layout from '@theme/Layout';
import Heading from '@theme/Heading';
import CodeBlock from '@theme/CodeBlock';
import styles from './index.module.css';

const manuals = [
  {
    number: '01', path: 'cli/', title: 'The operator manual', status: 'ACTIVE',
    text: 'Your workspace, models, sessions, and commands. Everything you need at the prompt.',
    link: '/docs/cli/overview', command: 'open cli', accent: 'cyan',
  },
  {
    number: '02', path: 'core/', title: 'Inside the runtime', status: 'ACTIVE',
    text: 'Follow the agent deck, Context Loom, and the ProtoLink engine behind every run.',
    link: '/docs/core/overview', command: 'open core', accent: 'magenta',
  },
  {
    number: '03', path: 'acp/', title: 'The editor bridge', status: 'PLANNED',
    text: 'The next interface. Explore the plan for bringing ProtoAgent into your editor.',
    link: '/docs/acp/overview', command: 'view roadmap', accent: 'amber',
  },
];

const runPath = [
  ['Context Loom', 'Collect bounded, source-cited evidence.'],
  ['Architect', 'Plan the task. Delegate narrow roles.'],
  ['Explorer / Coder', 'Read the workspace. Prepare a change.'],
  ['Approval gate', 'Review the diff before a write runs.'],
];

function TerminalPreview() {
  return (
    <section className={styles.terminal} aria-label="Illustrative ProtoAgent terminal session">
      <div className={styles.terminalTitle}>
        <span>PROTOAGENT TERMINAL</span>
        <span className={styles.previewLabel}>EXAMPLE SESSION</span>
      </div>
      <div className={styles.terminalInfo}>
        <span>project <b>~/projects/my-app</b></span>
        <span>profile <b className={styles.cyan}>small</b></span>
      </div>
      <div className={styles.terminalTabs} aria-label="Explore terminal commands">
        <Link to="/docs/cli/projects-and-sessions">/project</Link>
        <Link to="/docs/cli/models-and-config">/models</Link>
        <Link to="/docs/core/agents" className={styles.selectedTab}>/agents</Link>
        <Link to="/docs/cli/context-loom">/context</Link>
      </div>
      <div className={styles.transcript}>
        <p className={styles.prompt}><span aria-hidden="true">❯</span> explain <mark>@src/auth.rs</mark> and propose a safer diff</p>
        <div className={styles.event}><span className={styles.green}>context</span><span>Workspace evidence attached.</span></div>
        <div className={styles.event}><span className={styles.cyan}>architect</span><span>Inspect the flow. Delegate the change.</span></div>
        <div className={styles.workerTree}>
          <div><span aria-hidden="true">├─ </span><b>explorer</b><span>read + search</span></div>
          <div><span aria-hidden="true">└─ </span><b>coder</b><span>prepare diff</span></div>
        </div>
        <div className={styles.approval}><span aria-hidden="true">◇</span><span>workspace.write <b>requires your approval</b></span></div>
      </div>
      <div className={styles.terminalPrompt}><span aria-hidden="true">❯</span><span>Your next move<span className={styles.cursor} aria-hidden="true">▋</span></span></div>
      <div className={styles.terminalStatus}><span>LOCAL-FIRST / HUMAN IN CONTROL</span><span>Rust + Python</span></div>
    </section>
  );
}

export default function Home() {
  return (
    <Layout title="Operator manual" description="The ProtoAgent operator manual. A local-first Rust terminal, narrow agents, visible context, and approval-gated changes.">
      <main className={styles.workspace}>
        <div className={styles.pathBar}>
          <span><span className={styles.pathRoot}>~/protoagent</span> / docs</span>
          <span className={styles.version}>v0.2.0 <span aria-hidden="true">/</span> OPERATOR MANUAL</span>
        </div>
        <section className={styles.hero} aria-labelledby="home-title">
          <div className={styles.heroCopy}>
            <p className={styles.eyebrow}><span aria-hidden="true">[</span> LOCAL-FIRST AGENT CONSOLE <span aria-hidden="true">]</span></p>
            <Heading as="h1" id="home-title" className={styles.title}>Small models.<br />Full control<span className={styles.titleCursor} aria-hidden="true">_</span></Heading>
            <p className={styles.subtitle}>A fast Rust terminal. A Python core powered by ProtoLink. Narrow agents, visible context, and the final say on every write.</p>
            <div className={styles.actions}>
              <Link className={styles.primaryAction} to="/docs/getting-started/installation"><span aria-hidden="true">❯</span> Get started <span aria-hidden="true">↗</span></Link>
              <Link className={styles.secondaryAction} to="/docs/cli/commands">Command reference <span aria-hidden="true">→</span></Link>
            </div>
            <div className={styles.heroMeta}><span>RUST CLI</span><span>PYTHON CORE</span><span>MIT LICENSE</span></div>
          </div>
          <TerminalPreview />
        </section>
        <section className={styles.manualSection} aria-labelledby="manual-title">
          <div className={styles.sectionHeading}>
            <Heading as="h2" id="manual-title"><span aria-hidden="true">01 /</span> Open the manual</Heading>
            <Link to="/docs/intro">Repository map <span aria-hidden="true">↗</span></Link>
          </div>
          <div className={styles.manuals}>
            {manuals.map((manual) => (
              <Link to={manual.link} className={styles.manual} data-accent={manual.accent} key={manual.path}>
                <div className={styles.manualMeta}><span>{manual.number} <span aria-hidden="true">──</span> {manual.path}</span><span className={styles.moduleStatus}>{manual.status}</span></div>
                <Heading as="h3">{manual.title}</Heading>
                <p>{manual.text}</p>
                <span className={styles.manualCommand}><span><span aria-hidden="true">$ </span>{manual.command}</span><span aria-hidden="true">↗</span></span>
              </Link>
            ))}
          </div>
        </section>
        <section className={styles.quickStart} aria-labelledby="start-title">
          <div className={styles.launch}>
            <div className={styles.sectionHeading}><Heading as="h2" id="start-title"><span aria-hidden="true">02 /</span> Take the controls</Heading></div>
            <p>Once <Link to="/docs/getting-started/installation">installed from source</Link>, choose your workspace and model. Run these from the repository root.</p>
            <CodeBlock language="bash" title="shell / first session">{`# Set your workspace
cargo run --locked --manifest-path cli/Cargo.toml -- project set ~/projects/my-app

# Choose a provider and model
cargo run --locked --manifest-path cli/Cargo.toml -- model

# Open the terminal
cargo run --locked --manifest-path cli/Cargo.toml -- start`}</CodeBlock>
            <Link className={styles.walkthrough} to="/docs/getting-started/first-run">Follow your first run <span aria-hidden="true">→</span></Link>
          </div>
          <aside className={styles.runPath} aria-labelledby="run-path-title">
            <div className={styles.runPathHeading}><Heading as="h3" id="run-path-title">Behind the prompt</Heading><span>RUN PATH</span></div>
            <ol>
              {runPath.map(([title, description], i) => <li key={title}><span className={styles.stepNumber}>0{i + 1}</span><div><b>{title}</b><p>{description}</p></div></li>)}
            </ol>
            <Link to="/docs/core/runtime">Trace the runtime <span aria-hidden="true">↗</span></Link>
          </aside>
        </section>
        <div className={styles.endLine}><span aria-hidden="true">[ EOF ]</span><span>Read the source. Understand the system. Make it yours.</span></div>
      </main>
    </Layout>
  );
}
