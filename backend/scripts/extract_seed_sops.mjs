// Extract SEED_SOPS from valmo-platform's seedSops.js (ES module) to JSON on stdout.
// Used by ingest_knowledge.py so the platform's authored SOPs join the corpus.
// Usage: node extract_seed_sops.mjs <path-to-seedSops.js>
import { pathToFileURL } from "node:url";
const path = process.argv[2];
const mod = await import(pathToFileURL(path).href);
process.stdout.write(JSON.stringify(mod.SEED_SOPS || mod.default || []));
