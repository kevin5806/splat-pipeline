<script>
	// Legenda dei modelli di camera e del "dispositivo": compare dove si scelgono.
	let { open = false } = $props();
</script>

<details class="help" {open}>
	<summary>Guida: modelli di camera e dispositivi</summary>
	<div class="grid" style="gap: 12px; margin-top: 10px">
		<p class="small" style="margin: 0">
			Il <strong>modello della camera</strong> descrive come l'obiettivo deforma l'immagine. Non devi conoscere i valori: COLMAP li stima dalle
			foto. Serve solo a dirgli che <em>forma</em> di deformazione cercare. Se lasci "modello predefinito" si usa quello delle impostazioni del
			progetto (OPENCV).
		</p>

		<div class="grid" style="gap: 8px">
			<div class="card" style="padding: 10px 12px">
				<div class="row"><strong>OPENCV</strong><span class="chip ok">predefinito</span></div>
				<div class="small">
					<strong>Quando:</strong> telefoni e fotocamere con obiettivo normale o grandangolo moderato. Va bene anche per l'ultra-grandangolo di un
					telefono, perché di solito l'app lo raddrizza già, e per una GoPro in modalità «Lineare».
				</div>
				<div class="small mut">Stima 2 lunghezze focali, il centro dell'obiettivo, 2 coefficienti di distorsione radiale e 2 tangenziali (8 parametri).</div>
			</div>
			<div class="card" style="padding: 10px 12px">
				<div class="row"><strong>SIMPLE_RADIAL</strong></div>
				<div class="small">
					<strong>Quando:</strong> lenti con poca distorsione, oppure pochi frame o poca varietà di inquadrature. Meno parametri da stimare vuol dire un
					risultato più stabile quando i dati sono pochi.
				</div>
				<div class="small mut">Stima 1 focale, il centro e 1 coefficiente radiale (4 parametri). Meno preciso se la lente deforma molto.</div>
			</div>
			<div class="card" style="padding: 10px 12px">
				<div class="row"><strong>OPENCV_FISHEYE</strong><span class="chip warn">non ancora provato qui</span></div>
				<div class="small">
					<strong>Quando:</strong> obiettivi fisheye o grandangolo estremo <em>non raddrizzato</em>, per esempio una GoPro in «Wide/Largo» o
					«SuperView» (campo di circa 120° o più). I bordi sono molto curvi.
				</div>
				<div class="small mut">
					Usa una proiezione fisheye con 4 coefficienti. Con la GoPro in «Lineare» l'immagine è già raddrizzata: usa OPENCV. In questo progetto
					questo modello non è stato ancora verificato con il training.
				</div>
			</div>
		</div>

		<p class="small" style="margin: 0">
			<strong>Se sbagli modello</strong> non si rompe tutto, ma COLMAP può agganciare meno camere o ricostruire una stanza deformata. Il test di qualità
			prima del training serve anche a questo.
		</p>

		<hr style="border: 0; border-top: 1px solid var(--line); width: 100%; margin: 0" />

		<div class="small">
			<strong>Dispositivo</strong> (numero da 1 a 8): i video girati con <strong>la stessa camera e le stesse impostazioni</strong> hanno lo stesso numero e
			condividono una camera in COLMAP. Se unisci un telefono e una GoPro, metti 1 e 2.
			<ul style="margin: 6px 0 0; padding-left: 18px">
				<li>Cambiare zoom o obiettivo (principale / ultra-grandangolo) cambia la focale: conta come <strong>un dispositivo diverso</strong>.</li>
				<li>Video dello stesso dispositivo devono avere la <strong>stessa risoluzione</strong> e lo <strong>stesso modello</strong>, altrimenti il progetto si ferma con un messaggio chiaro.</li>
				<li>I video devono riprendere in parte le <strong>stesse zone</strong>: se non hanno nulla in comune COLMAP non riesce a unirli.</li>
			</ul>
		</div>
	</div>
</details>

<style>
	.help {
		border: 1px solid var(--line);
		border-radius: 10px;
		padding: 10px 12px;
		background: var(--card);
	}
	.help summary {
		cursor: pointer;
		font-weight: 600;
	}
</style>
