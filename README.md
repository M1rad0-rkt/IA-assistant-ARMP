# IA-assistant-ARMP
"""
prepare_manuals.py - Prépare les manuels e-GP / SIGMP (et le guide de 2006) avant de les
envoyer dans AnythingLLM ou dans notre propre index.

Ce que fait le script
  1. Lit chaque PDF avec pdfplumber (corrige les lettres doublées du manuel SIGMP).
  2. Supprime le bruit : pied de page, sommaire, listes de figures.
  3. Découpe par procédure ("Comment ... ?") en suivant le sommaire du manuel :
     un fichier .md par section, avec un en-tête (plateforme, module, chemin, pages).
  4. Convertit les tableaux de droits par profil en phrases ("Profil X : consultation, création").
  5. Écrit un rapport (_rapport.md) et un index (_index.csv) : sections vides, titres non
     retrouvés, sections presque sans texte (étapes probablement dans des captures d'écran).

Utilisation
    python src/prepare_manuals.py --raw data/raw --out data/prepared
    python src/prepare_manuals.py --raw /chemin/vers/pdf --out sortie --only egp,sigmp
"""

# Données brutes
data/raw/guide_manuels
  - BENEFICIAIRE_EFFECTIF_A_L_eGP
  - e-GP_MANUEL_UTILISATEUR_V_1_B
  - GUIDE-UTILISATEUR-SUR-LA-PASSATION-DE-MARCHE
  - SIGMP_Manu_Util_2_0_Provisoire
