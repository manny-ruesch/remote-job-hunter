# Produire le GIF du README (Noir Webling)

Objectif : 8 à 10 secondes, 760 px de large, **moins de 3 Mo** (GitHub et PyPI chargent l'image à chaque visite).

Scénario conseillé : la toile et les araignées au repos → clic sur « Lancer le scan » (l'onde sonar, les araignées s'affolent)
→ une carte HOT descend au bout de son fil → le score s'affiche.

```bash
# 1. enregistrez l'écran en MP4 (OBS, Win+G, QuickTime…), puis :
ffmpeg -i demo.mp4 -t 10 -vf "fps=15,scale=760:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=4" -loop 0 docs/assets/noir-webling-demo.gif
```

Le fichier d'origine `noir-webling.webp` pèse 1,7 Mo : ne le servez pas tel quel dans le dashboard, redimensionnez-le (192 px suffisent).
