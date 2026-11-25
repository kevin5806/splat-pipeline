<script>
	// Legenda dei modelli di camera e del "dispositivo": compare dove si scelgono.
	let { open = false } = $props();
</script>

<details class="help" {open}>
	<summary>Guida: modelli di camera, passo e dispositivi</summary>
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
			<strong>Passo</strong> (un frame ogni N): di un video si prende un fotogramma ogni N, non tutti. Con un video a 30 fps:
			<table style="border-collapse: collapse; margin: 6px 0; width: 100%">
				<thead>
					<tr style="text-align: left; color: var(--mut)"><th>Passo</th><th>Frame al secondo</th><th>Video 4K di 7 min (13.242 frame)</th></tr>
				</thead>
				<tbody>
					<tr><td><strong>1</strong></td><td>30 (tutti)</td><td>13.242 · <span style="color: var(--bad)">sconsigliato</span></td></tr>
					<tr><td><strong>2</strong></td><td>15</td><td>6.621 · pesantissimo</td></tr>
					<tr><td><strong>4</strong></td><td>7,5</td><td>3.311 · molto lento</td></tr>
					<tr><td><strong>8</strong></td><td>3,75</td><td>1.656</td></tr>
					<tr><td><strong>16</strong></td><td>1,9</td><td>828 (valore predefinito)</td></tr>
					<tr><td><strong>30</strong></td><td>1</td><td>442 · poca sovrapposizione se ti muovi veloce</td></tr>
				</tbody>
			</table>
			Più frame = più sovrapposizione tra le foto, ma COLMAP e il training crescono <em>più che proporzionalmente</em> e la RAM può finire. Frame
			consecutivi a 30 fps sono quasi identici: dopo un certo punto aggiungerne non migliora. Come ordine di grandezza, con 1.842 frame il matching
			è durato circa 2,5 ore e il bundle adjustment oltre un'ora. Se non sai quanti metterne, usa «Frame totali desiderati».
		</div>

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
