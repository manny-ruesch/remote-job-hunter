# Sécurité

## Signaler une faille
Utilisez **« Report a vulnerability »** (onglet Security du dépôt, signalement privé). N'ouvrez pas d'issue publique.
Nous accusons réception sous 7 jours. <!-- TODO : adaptez le délai à ce que vous pouvez tenir -->

## Ce que le projet garantit (et sera corrigé s'il ne le fait pas)
- `rjh serve` refuse d'écouter hors localhost sans `RJH_TOKEN`.
- Les clés d'API ne sont jamais écrites dans `profile.yaml`, ni dans une URL, un log ou la sortie de `rjh doctor`.
- Le `.env` est créé en droits 600.

## Ce qui reste votre responsabilité
- N'exposez jamais le port du Command Center tel quel sur Internet : mettez un reverse proxy HTTPS ou un réseau privé (Tailscale…) devant.
- Les plugins de sources (`load_plugins`) s'exécutent avec vos droits : ne chargez que du code de confiance.
