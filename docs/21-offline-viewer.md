# Offline viewer (one file)

The hosted viewer asks the API for every page, which is slow when the free Render service is asleep.
`fbdb.export.viewer_file` writes the whole database into ONE HTML file. Double-click it: no server, no internet, instant search.

    DATABASE_URL=postgresql://... PYTHONPATH=src python -m fbdb.export.viewer_file --out data/fbdb_viewer.html
    # quick test with a few rows: add --limit 200

What is in it: the same business fields as the public API and the same detail panel (financial exercises, certificates,
activities and declarations read from the site, legal relations, register events). No phone, email, LinkedIn, director
names or Google data; sole proprietors and non-diffusible units are left out. Keep the file private anyway.

"Nothing recorded" in a section means the enrichment step for that section has not been run (or found nothing) for that
company. It is not a viewer bug. See docs/19-enrichment-gaps.md for which steps are verified live.
