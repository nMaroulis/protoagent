import clsx from 'clsx';
import Link from '@docusaurus/Link';
import Layout from '@theme/Layout';
import Heading from '@theme/Heading';
import styles from './index.module.css';

const sections = [
  {
    title: 'CLI Operator Manual',
    text: 'Commands, fullscreen TUI panels, project selection, sessions, model setup, context controls, approvals, cancellation, and trace output.',
    link: '/docs/cli/overview',
    cta: 'Open CLI docs',
  },
  {
    title: 'Core Runtime',
    text: 'PyO3 entrypoints, ProtoLink mesh execution, RunContext, RunEvent, RunReport, state compaction, Context Loom, and safe workspace tools.',
    link: '/docs/core/overview',
    cta: 'Open core docs',
  },
  {
    title: 'ACP Roadmap',
    text: 'Current implementation status is intentionally marked TBD, with the planned editor bridge shape documented separately.',
    link: '/docs/acp/overview',
    cta: 'Open ACP plan',
  },
];

export default function Home() {
  return (
    <Layout
      title="ProtoAgent Docs"
      description="Documentation for the ProtoAgent monorepo, CLI, Python core, and ACP roadmap.">
      <header className={styles.hero}>
        <div className={styles.heroOverlay}>
          <div className={styles.heroInner}>
            <p className={styles.eyebrow}>Local-first agent console</p>
            <Heading as="h1" className={styles.title}>
              ProtoAgent Docs
            </Heading>
            <p className={styles.subtitle}>
              The modular manual for the Rust CLI, ProtoLink-powered Python core, Context Loom, and the editor-facing ACP plan.
            </p>
            <div className={styles.actions}>
              <Link className="button button--primary button--lg" to="/docs/intro">
                Read the docs
              </Link>
              <Link className="button button--secondary button--lg" to="/docs/cli/commands">
                Command reference
              </Link>
            </div>
          </div>
        </div>
      </header>
      <main>
        <section className={styles.band}>
          <div className={styles.sectionHeader}>
            <p className={styles.eyebrow}>Monorepo map</p>
            <Heading as="h2">Three surfaces, one runtime</Heading>
          </div>
          <div className={styles.cards}>
            {sections.map((section) => (
              <article className={clsx(styles.card)} key={section.title}>
                <Heading as="h3">{section.title}</Heading>
                <p>{section.text}</p>
                <Link to={section.link}>{section.cta}</Link>
              </article>
            ))}
          </div>
        </section>
        <section className={styles.terminalBand}>
          <div className={styles.terminal}>
            <div className={styles.terminalTop}>PROTOAGENT TERMINAL</div>
            <pre>
{`$ proto-cli project set ~/projects/my-app
$ proto-cli model
$ proto-cli run "explain the auth flow and propose a safer diff"

RunContract -> Architect -> stateless workers
Context Loom evidence
Approval gate + completion guard`}
            </pre>
          </div>
          <div className={styles.terminalCopy}>
            <p className={styles.eyebrow}>Built for updating</p>
            <Heading as="h2">Docs mirror the code boundaries</Heading>
            <p>
              Each page maps back to a small set of source files. When a command, agent, config key, or runtime bridge changes, the reference section points at the doc page that should move with it.
            </p>
          </div>
        </section>
      </main>
    </Layout>
  );
}
