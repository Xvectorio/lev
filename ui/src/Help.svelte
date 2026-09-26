<script lang="ts" module>
  // Inline docs, condensed from docs/guide.md (keep them in sync). A line starting with "$ " is a command, "- " a list item, "# " a subheading.
  const TOPICS: Record<string, { title: string; body: string[] }> = {
    incidents: { title: 'How incidents work', body: [
      'Vector ships warn/error/fatal lines to Loki. Every 60 s the worker groups them by fingerprint (IDs, UUIDs, hex values and durations are normalized), so repeats of one problem become one incident with a count.',
      '# Stages',
      '- new: grouped, Jev triage pending.',
      '- ready: Jev found it actionable with enough confidence; an agent can investigate.',
      '- review: Jev was uncertain, or a fix failed its checks. You decide.',
      '- observing: harmless, transient, or dismissed. New occurrences only update the count; use Triage again to re-judge.',
      '- proposed: an agent submitted a diagnosis, exact changes, checks and a rollback plan. Approve or dismiss it.',
      '- approved: the agent may apply exactly that proposal.',
      '- verifying: the agent reported every check as passed; Lev watches for recurrence.',
      '- resolved: 15 minutes with no recurrence and a live heartbeat from the source.',
      'If the problem comes back, the incident reopens as a new episode and earlier approvals are invalidated. Lev itself never runs commands.',
      '# Your verdict',
      'In an incident you can record where Jev should have routed it. Verdicts feed the Jev tune wizard and never change the incident.',
      '# Fields',
      'Fields counts incidents across all pages. Most affected services ranks services over every server; By source breaks them down per project and server. The bar is the share of its parent (or of all incidents), its red part the share at error level. Click a name to filter the list, click it again to remove the filter.',
    ]},
    logs: { title: 'Searching logs', body: [
      'Searches Loki, which keeps raw logs for 48 hours by default (LOG_RETENTION_HOURS). Only warn, error and fatal lines (plus a heartbeat, hidden unless you filter service=lev-heartbeat) are stored.',
      '# Query syntax',
      '- Words must all match: timeout upstream',
      '- "quoted phrase" matches exactly.',
      '- NOT excludes the next term: error NOT healthcheck',
      '- field=value filters on a label: server_id, project_id, host, service, environment, level. Completed filters become chips; Backspace removes the last one.',
      'Click a histogram bar to zoom into that time range, or a name under Fields to filter on it (click again to remove). Fields ranks the most affected services and breaks the loaded events down per project, server and service. The histogram covers the loaded events only, not total volume.',
    ]},
    sources: { title: 'Sources and collection', body: [
      'Every server runs Vector, which reads journald, Docker and log files, joins stack traces, redacts secrets and forwards only warn+ lines plus a heartbeat once a minute. A source is live while its heartbeat is recent; incidents only resolve while their source is live.',
      'This Lev host already collects its own journal and /var/log/apps/<service>/*.log as server local.',
      'The small ✕ next to a source or one of its logs hides it here and in the Fields columns (this browser only); Settings → Hidden sources and logs unhides them.',
      'See the ? on Vector sources for adding a server, and on Connect for agents.',
    ]},
    collection: { title: 'How collection works', body: [
      'The worker polls Loki every pass from a saved nanosecond checkpoint, re-reading an overlap window (LOOKBACK_SECONDS) to catch late events, and skips the last settling seconds that may still be arriving. Duplicate events count once.',
      'After an outage it catches up in bounded steps. Events delayed longer than the overlap stay searchable but may not become incidents; raise LOOKBACK_SECONDS in .env if shipping is routinely slow.',
      'Collection never waits for Jev or the AI model.',
      'An outage longer than the raw log retention (LOG_RETENTION_HOURS, default 48 h) loses the expired logs; the worker reports this.',
    ]},
    'add-source': { title: 'Add a source server', body: [
      'The easiest route is Claude Code with the lev-vector skill ("install Vector and watch nginx"). By hand:',
      '- Download vector-compose.yaml and vector.env.example from the latest GitHub release into /opt/lev-vector, rename them to compose.yaml and .env, and create an empty watch.d/ directory.',
      '$ chmod 600 .env',
      '- In .env set VECTOR_PROJECT_ID (e.g. payments-prod) and VECTOR_SERVER_ID (e.g. eu-west-1-app-03): stable, low-cardinality IDs.',
      '- Set VECTOR_HOST (hostname), VECTOR_ENVIRONMENT, and JOURNAL_GID:',
      '$ getent group systemd-journal',
      '- VECTOR_ENDPOINT: this Lev URL (https://…), without /loki/api/v1/push.',
      '- VECTOR_PASSWORD: Sources → Connect → Copy ingest password.',
      '$ docker compose up -d',
      'The server appears in this table after its first heartbeat (about a minute). Collection starts at the current end of each log, not its history.',
      '# Extra log sources',
      'Add a file per service in watch.d/<name>.yaml (a src_<name> source plus a watch_<name> remap setting .lev_service) with read-only mounts in compose.override.yaml. Never edit the shared vector.yaml; to quiet noise a server already collects (e.g. UFW blocks), put VRL that lowers .level in watch.d/local.vrl. Include tests: with real sample lines and check that secrets are redacted.',
      'Do not collect the same app through both its file and journal source. Container IDs change on redeploy; refresh container watch files afterwards.',
      '# Without Docker',
      'On a systemd server without Docker, download vector-install.sh from the same release and run it as root. It installs the matching Vector package with Lev\'s config in /etc/vector/. Set the same values (without JOURNAL_GID) in /etc/default/vector, then:',
      '$ systemctl enable --now vector',
      'Watch files go in /etc/vector/watch.d/ and need no mounts, but the vector user must be able to read the files they watch. Docker container logs are not collected. Rerun a newer vector-install.sh to upgrade.',
    ]},
    connect: { title: 'Connecting agents', body: [
      'Lev does not fix things itself. An external agent (e.g. Claude Code with the lev-agent skill) fetches ready tasks, investigates read-only, submits a proposal, waits for your approval, then applies it and reports every check.',
      '- Copy the lev-agent skill folder to ~/.claude/skills/ on the machine that runs the agent.',
      '- Set LEV_URL to this Lev and LEV_AGENT_TOKEN to Copy agent token.',
      '$ /loop 30m /lev-agent',
      'On a source server the agent only picks up incidents from its own VECTOR_SERVER_ID. Use one agent per server. The token grants task access but never approval; treat it as a credential.',
      'Copy ingest password is for VECTOR_PASSWORD on source servers.',
    ]},
    jev: { title: 'How Jev triage works', body: [
      'Jev (TypeSafe) answers two typed questions per incident episode: which category it is, and whether it needs action (investigate / observe / unknown), each with a confidence.',
      '- investigate ≥ ready gate → ready',
      '- observe ≥ observe gate → observing',
      '- otherwise → review, so uncertain cases reach a human instead of being guessed.',
      'An optional AI text model (Settings) then writes the summary and suspected cause. Pausing stops provider calls only; collection continues and jobs wait. Each triage costs Jev credits.',
      'Activity shows, for the chosen timeframe, the log lines collected, incidents triaged, Jev calls, input tokens and an estimated cost at the TypeSafe list price (input tokens only; output is free). Many lines share one incident, so calls stay far below lines.',
      'The question wording, categories and gates are the policy (Policy tab). Improve it with your verdicts in the Tune wizard.',
    ]},
    policy: { title: 'Editing the policy', body: [
      'The policy is the wording Jev sees plus the gates that turn its answers into routes. Versions are immutable: saving creates a new numbered version, and activating an older one is a rollback.',
      '- Description: what belongs in a category. Not for: what to send elsewhere. Examples: real log lines, one per line. These sharpen confusable categories most.',
      '- Suggested checks go to the agent for incidents in that category.',
      '- Actionability options are fixed because routing depends on them; their meaning is yours.',
      'New triage uses the active version immediately; existing incidents keep their judgment until Triage again. Safer: save as inactive, test it in the Tune wizard (step 5), then activate.',
      'Suggest wording with AI sends the draft and misjudged incidents to the AI model from Settings. Suggestions only fill the draft once you accept them.',
    ]},
    tune: { title: 'Tuning Jev', body: [
      '- 1 · See where triage disagrees with your verdicts and where it is unsure.',
      '- 2 · Give verdicts: most informative first. 20–50 verdicts make the numbers meaningful.',
      '- 3 · Move the gates: re-routes stored answers, free and instant. Higher ready gate = fewer false ready, more review.',
      '- 4 · Wrong categories need better wording, not gates: edit the policy or ask AI for suggestions.',
      '- 5 · Test a saved version against your verdicts before activating it. Costs one Jev call per incident per version and only runs while no live triage is waiting.',
    ]},
    settings: { title: 'AI providers', body: [
      'Values saved here override .env; clear one to fall back to it. Keys are stored in the database and never shown again.',
      '- TypeSafe API key: required for Jev triage. Without it jobs stay queued.',
      '- AI key / model: any OpenAI-compatible endpoint for incident explanations and policy suggestions. Optional.',
      'Provider base URLs (AI_BASE_URL, e.g. https://openrouter.ai/api/v1, and TYPESAFE_BASE_URL) are .env-only, so the keys can only go where the server owner points them. Domain, ports and HTTPS are container settings: change them in .env and run docker compose up -d.',
      '# Data',
      '- Load test data: an hour of realistic logs from six made-up servers (about 35 incidents across every category, some deliberately borderline), ingested like real ones. Jev triages them (spends credits if a key is set).',
      '- Delete all data: removes every incident, verdict, audit entry and every log in Loki, real ones too. Users, settings and policies stay. Type DELETE ALL DATA to confirm; only a backup undoes it.',
      '# Retention',
      'Shown under Settings → Retention. Set in .env, then run docker compose up -d:',
      '- LOG_RETENTION_HOURS (default 48, minimum 24): raw logs in Loki and the longest search range. Collected incident evidence is pruned a day later; incident examples, verdicts and audit are kept.',
      '- BACKUP_RETENTION_DAYS (default 14): daily database dumps in backups/.',
      'Incidents, verdicts and audit stay in PostgreSQL until you delete them.',
    ]},
  };
</script>

<script lang="ts">
  let { topic }: { topic: string } = $props();
  const id = $props.id();
  const t = $derived(TOPICS[topic]);
</script>

{#if t}
  <button class="help-icon" popovertarget={id} aria-label="Help: {t.title}" title="Help: {t.title}">?</button>
  <div class="help-panel" popover {id}>
    <div class="help-head"><h2>{t.title}</h2><button class="clear" popovertarget={id} popovertargetaction="hide" aria-label="Close help">×</button></div>
    {#each t.body as line}
      {#if line.startsWith('$ ')}<pre><code>{line.slice(2)}</code></pre>
      {:else if line.startsWith('- ')}<p class="help-item">{line.slice(2)}</p>
      {:else if line.startsWith('# ')}<h3>{line.slice(2)}</h3>
      {:else}<p>{line}</p>{/if}
    {/each}
  </div>
{/if}
