<script>
	import { fmtClock, apiUrl } from '#lib/api';

	// report: esito del recupero per questo video (può mancare); ranges: tratti senza camere dal bundle adjustment;
	// views: viste del test di quel video (per la striscia della qualità)
	let { name, duration = 0, report = null, ranges = [], views = [] } = $props();

	const LABEL = {
		fixed: { icon: '✓', text: 'sistemato', cls: 'ok' },
		improved: { icon: '◐', text: 'migliorato', cls: 'warn' },
		pending: { icon: '◐', text: 'poco coperto, da sistemare', cls: 'warn' },
		unchanged: { icon: '✕', text: 'ancora poco coperto', cls: 'bad' },
		new: { icon: '✕', text: 'poco coperto', cls: 'bad' },
		lost: { icon: '✕', text: 'camere non agganciate', cls: 'bad' }
	};

	let when = $state('after'); // 'before' | 'after'
	let tip = $state(null); // { x: percentuale, lines, thumb, image }

	const sections = $derived(
		report
			? report.sections
			: (ranges ?? []).map((r) => ({ start_s: r[0], end_s: r[1], status: 'lost', before_s: r[1] - r[0], after_s: r[1] - r[0] }))
	);
	const canCompare = $derived(!!report && report.sections.some((s) => s.status !== 'pending') && !report.support);
	const shown = $derived(
		when === 'before' && canCompare
			? sections.filter((s) => s.before_s > 0).map((s) => ({ ...s, status: 'lost' }))
			: sections
	);
	const used = $derived([...new Set(shown.map((s) => LABEL[s.status].cls))]);
	const pos = (t) => Math.max(0, Math.min(100, (100 * t) / (duration || 1)));
	const secs = (s) => `${s.toLocaleString('it-IT', { maximumFractionDigits: 1 })} s`;

	const lo = $derived(views.length ? Math.min(...views.map((v) => v.psnr)) : 0);
	const hi = $derived(views.length ? Math.max(...views.map((v) => v.psnr)) : 1);
	const height = (v) => 15 + (85 * (v - lo)) / Math.max(0.1, hi - lo);

	function showSection(s) {
		const lines = [`${fmtClock(s.start_s)}–${fmtClock(s.end_s)}`, `${LABEL[s.status].icon} ${LABEL[s.status].text}`];
		if (report && when === 'after' && s.status !== 'pending') lines.push(`scoperti: ${secs(s.before_s)} → ${secs(s.after_s)}`);
		tip = { x: pos((s.start_s + s.end_s) / 2), lines, thumb: s.thumb ? apiUrl(`/projects/${name}/thumb/${s.thumb}`) : null };
	}
	function showView(v) {
		tip = {
			x: pos(v.t),
			lines: [`${fmtClock(v.t)} · PSNR ${v.psnr} dB`, v.file ? 'clic: confronto foto / modello' : ''],
			thumb: v.file ? apiUrl(`/projects/${name}/quality/test/${v.file}`) : null,
			wide: true
		};
	}
	const hide = () => (tip = null);
</script>

{#if duration > 0}
	<div class="tl">
		<div class="row small" style="gap: 10px">
			<span class="mut">Copertura lungo il video</span>
			{#each used as cls}
				<span class="key"><i class={cls}></i>{cls === 'ok' ? '✓ sistemato' : cls === 'warn' ? '◐ migliorato o da sistemare' : '✕ poco coperto'}</span>
			{/each}
			<span class="key"><i class="base"></i>coperto</span>
			<span class="grow"></span>
			{#if canCompare}
				<span class="seg" role="group" aria-label="Prima o dopo il recupero">
					<button class:on={when === 'before'} onclick={() => (when = 'before')}>Prima</button>
					<button class:on={when === 'after'} onclick={() => (when = 'after')}>Dopo</button>
				</span>
			{/if}
		</div>
		<div class="track" onmouseleave={hide} role="presentation">
			{#each shown as s}
				<button
					class="mark {LABEL[s.status].cls}"
					style="left: {pos(s.start_s)}%; width: {Math.max(0, pos(s.end_s) - pos(s.start_s))}%"
					aria-label="{fmtClock(s.start_s)}–{fmtClock(s.end_s)}: {LABEL[s.status].text}"
					onmouseenter={() => showSection(s)}
					onfocus={() => showSection(s)}
					onblur={hide}
				></button>
			{/each}
		</div>
		{#if views.length}
			<div class="small mut">Qualità del test lungo il video (barra più alta = meglio; da {lo} a {hi} dB)</div>
			<div class="strip" onmouseleave={hide} role="presentation">
				{#each views as v}
					<a
						style="left: {pos(v.t)}%; height: {height(v.psnr)}%"
						href={v.file ? apiUrl(`/projects/${name}/quality/test/${v.file}`) : undefined}
						target="_blank"
						rel="noopener"
						aria-label="{fmtClock(v.t)}: PSNR {v.psnr} dB"
						onmouseenter={() => showView(v)}
						onfocus={() => showView(v)}
						onblur={hide}
					></a>
				{/each}
			</div>
		{/if}
		<div class="row small mut" style="justify-content: space-between"><span>0:00</span><span>{fmtClock(duration)}</span></div>
		{#if tip}
			<div class="tip" class:wide={tip.wide} style="left: clamp(0px, calc({tip.x}% - {tip.wide ? 160 : 90}px), calc(100% - {tip.wide ? 320 : 180}px))">
				{#each tip.lines.filter(Boolean) as l}<div>{l}</div>{/each}
				{#if tip.thumb}<img src={tip.thumb} alt="" />{/if}
			</div>
		{/if}
	</div>
{/if}

<style>
	.tl { position: relative; display: grid; gap: 4px; }
	.track { position: relative; height: 16px; background: var(--line); border-radius: 4px; }
	.mark { position: absolute; top: 0; bottom: 0; min-width: 5px; padding: 0; border: 0; border-radius: 3px; box-shadow: 0 0 0 1px var(--card); cursor: pointer; }
	.mark.ok, i.ok { background: var(--ok); }
	.mark.warn, i.warn { background: var(--warn); }
	.mark.bad, i.bad { background: var(--bad); }
	.mark:hover, .mark:focus-visible { outline: 2px solid var(--fg); outline-offset: 1px; z-index: 1; }
	.key { display: inline-flex; align-items: center; gap: 4px; }
	.key i { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
	.key i.base { background: var(--line); }
	.strip { position: relative; height: 34px; border-bottom: 1px solid var(--line); }
	.strip a { position: absolute; bottom: 0; width: 3px; margin-left: -1.5px; background: var(--acc); border-radius: 2px 2px 0 0; }
	.strip a:hover, .strip a:focus-visible { width: 6px; margin-left: -3px; z-index: 1; }
	.seg button { padding: 2px 10px; font-size: 12px; border-radius: 0; }
	.seg button:first-child { border-radius: 6px 0 0 6px; }
	.seg button:last-child { border-radius: 0 6px 6px 0; margin-left: -1px; }
	.seg button.on { border-color: var(--acc); color: var(--acc); }
	.tip { position: absolute; top: 100%; width: 180px; z-index: 20; background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 8px; font-size: 12px; box-shadow: 0 6px 24px rgba(0, 0, 0, 0.3); pointer-events: none; }
	.tip.wide { width: 320px; }
	.tip img { width: 100%; border-radius: 4px; display: block; margin-top: 6px; }
</style>
