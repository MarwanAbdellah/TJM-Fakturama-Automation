# Diagrams (hand-drawn)

Generated with rough.js. To regenerate:
`cd tools/diagrams && npm install && node make_diagrams.mjs`

## Architecture

The app and the CLI drive the same flow. Fakturama is only written through its UI.
The database is only read, to decide exact matches and to verify what was saved.

![architecture](architecture.svg)

## Business flow

This is the Order-first flow from the brief. Database counts decide between
select, create and manual review.

![flow](flow.svg)

## Control grounding

How each control is found, in order of preference. No coordinates are stored.

![grounding](grounding.svg)
