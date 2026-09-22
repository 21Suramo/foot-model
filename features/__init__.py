"""Ingénierie de features pour le pipeline ML (roadmap B1-B4, 2026-09-22).

Tout module de ce package respecte la même discipline anti-fuite que
model.py/backtest.py : chaque feature d'un match est calculée uniquement à
partir de matchs strictement antérieurs (jamais le match courant ni le
futur). `build.py` fait l'unique passe chronologique qui matérialise cette
garantie ; les sous-modules (elo, pi_rating, form, rest) sont des fonctions
de mise à jour d'état pures, sans accès direct à la base.
"""
