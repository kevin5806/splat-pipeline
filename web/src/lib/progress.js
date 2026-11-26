// Peso di ogni fase sul tempo totale (stima dai tempi misurati: il training domina).
const WEIGHT = {
	probe: 1,
	frames: 3,
	features: 5,
	matching: 12,
	mapping: 15,
	refine: 8,
	recover: 8,
	convert: 3,
	test: 6,
	train: 40,
	export: 7
};

export function overall(project) {
	// il totale si fa sulle fasi del progetto: con una fase nuova non si supera il 100%
	const total = project.stages.reduce((a, s) => a + (WEIGHT[s.id] ?? 1), 0) || 1;
	let done = 0;
	for (const s of project.stages) {
		const w = WEIGHT[s.id] ?? 1;
		if (s.status === 'done') done += w;
		else if (s.status === 'running') done += (w * (s.progress || 0)) / 100;
	}
	return Math.round((100 * done) / total);
}

export const DEFAULT_SETTINGS = () => ({
	frame_step: 16,
	target_frames: null,
	camera_model: 'OPENCV',
	matcher: 'vocab_tree',
	downscale: 2,
	steps: 30000,
	quality: 'standard',
	test_steps: 4000,
	skip_test: false,
	gate_psnr: 18.5,
	crop_radius: 3.5,
	cache_images: 'cpu'
});

export const chipClass = (status) =>
	({ done: 'ok', running: 'run', queued: 'run', failed: 'bad', blocked: 'warn', interrupted: 'warn', stopped: 'warn' })[status] || '';
