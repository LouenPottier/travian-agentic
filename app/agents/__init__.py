"""Phase 4 — agents LLM jouant via l'API du jeu.

`tools` : serveur MCP in-process exposant les **actions joueur légitimes** (chaque
outil forwarde vers l'endpoint HTTP existant → surface strictement identique à l'UI,
donc « sans tricher »). `macro` : registre + boucle pilotée par Claude Code (Claude
Agent SDK), lançable depuis le site.
"""
