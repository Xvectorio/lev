<script lang="ts">
  // Splunk-style query bar: field=value tokens become removable filter chips; other words must all match.
  let { id, fields, values, placeholder, text = $bindable(''), filters = $bindable({}) }: {
    id: string; fields: string[]; values: Record<string, string[]>; placeholder: string;
    text?: string; filters?: Record<string, string>;
  } = $props();
  let suggesting = $state(false), input = $state<HTMLInputElement>();
  const quote = (v: string) => /[\s"]/.test(v) ? JSON.stringify(v) : v;
  // Completed field=value tokens move out of the text into chips; `all` also takes the token still being typed.
  export function absorb(all = false) {
    const token = all ? /(^|\s)(\w+)=(?!\w+=)("(?:[^"\\]|\\.)*"|\S+)(?=\s|$)/g : /(^|\s)(\w+)=(?!\w+=)("(?:[^"\\]|\\.)*"|\S+)(?=\s)/g;
    const next = { ...filters };
    let rest = text.replace(token, (match, lead, key, raw) => {
      if (!fields.includes(key)) return match;
      try { next[key] = raw.startsWith('"') ? JSON.parse(raw) : raw; } catch { return match; }
      return lead;
    });
    if (all) { suggesting = false; rest = rest.replace(/(^|\s)(\w+)=(?=\s|$)/g, (match, lead, key) => fields.includes(key) ? lead : match).replace(/\s+/g, ' ').trim(); }
    if (rest !== text) { filters = next; text = rest.replace(/^\s+/, ''); }
  }
  function removeFilter(field: string) { const { [field]: _, ...kept } = filters; filters = kept; input?.focus(); }
  const lastToken = $derived(text.match(/\S*$/)?.[0] ?? '');
  const suggestions = $derived.by(() => {
    const pair = lastToken.match(/^(\w+)=(.*)$/);
    if (pair && values[pair[1]]) {
      const typed = pair[2].replace(/^"/, '').toLowerCase();
      return values[pair[1]].filter(v => v.toLowerCase().includes(typed) && quote(v) !== pair[2])
        .sort((a, b) => Number(!a.toLowerCase().startsWith(typed)) - Number(!b.toLowerCase().startsWith(typed))).slice(0, 12).map(v => `${pair[1]}=${quote(v)}`);
    }
    return lastToken ? fields.filter(f => f.startsWith(lastToken) && f !== lastToken).map(f => f + '=') : [];
  });
  function pick(suggestion: string) {
    text = text.replace(/\S*$/, suggestion) + (suggestion.endsWith('=') ? '' : ' ');
    absorb();
    input?.focus();
  }
  function keys(e: KeyboardEvent) {
    const buttons = [...document.querySelectorAll<HTMLElement>(`#${id}-suggestions button`)];
    const at = buttons.indexOf(document.activeElement as HTMLElement);
    if (e.key === 'Backspace' && e.target === input && !text) { const set = Object.keys(filters); if (set.length) { e.preventDefault(); removeFilter(set[set.length - 1]); } }
    else if (e.key === 'Escape') { suggesting = false; input?.focus(); }
    else if (e.key === 'ArrowDown' && buttons.length) { e.preventDefault(); suggesting = true; buttons[Math.min(at + 1, buttons.length - 1)].focus(); }
    else if (e.key === 'ArrowUp' && at >= 0) { e.preventDefault(); (at === 0 ? input : buttons[at - 1])?.focus(); }
  }
</script>

<div class="search-field" role="group" aria-label="Query" onfocusout={(e) => { if (!(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node)) suggesting = false; }}>
  <label for={id}>Search</label>
  <div class="query-box">
    {#each Object.entries(filters) as [field, value] (field)}<button type="button" class="token" aria-label={`Remove filter ${field}=${value}`} title="Remove filter" onclick={() => removeFilter(field)}>{field}=<b>{value}</b><span aria-hidden="true">×</span></button>{/each}
    <input {id} type="text" role="combobox" aria-autocomplete="list" autocomplete="off" spellcheck="false" bind:this={input} bind:value={text} onkeydown={keys} oninput={() => { suggesting = true; absorb(); }}
      placeholder={Object.keys(filters).length ? 'Add words or field=value' : placeholder} aria-describedby={`${id}-help`} aria-expanded={suggesting && suggestions.length > 0} aria-controls={`${id}-suggestions`}>
    {#if Object.keys(filters).length || text}<button type="button" class="clear" aria-label="Clear query" title="Clear query" onclick={() => { filters = {}; text = ''; input?.focus(); }}>×</button>{/if}
    {#if suggesting && suggestions.length}<div class="suggestions" id={`${id}-suggestions`} role="listbox" tabindex="-1" onkeydown={keys}>{#each suggestions as suggestion}<button type="button" role="option" aria-selected="false" onclick={() => pick(suggestion)}>{suggestion.endsWith('=') ? suggestion : suggestion.slice(suggestion.indexOf('=') + 1).replace(/^"|"$/g, '')}</button>{/each}</div>{/if}
  </div>
  <p id={`${id}-help`} class="query-help">Fields: {#each fields as f}<button type="button" onclick={() => { text = (text.trim() + ' ' + f + '=').trimStart(); suggesting = true; input?.focus(); }}>{f}</button>{/each} · words must all match · <code>"exact phrase"</code> · <code>NOT word</code></p>
</div>
