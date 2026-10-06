#!/usr/bin/env node
/**
 * Prints one JSON record per line on stdout and a JSON summary on stderr.
 * aggregator/ts_sources.py runs this, parses stdout, and feeds the records into the Python pipeline.
 */
import { runAll } from './run.js';

const { records, reports } = await runAll();
process.stdout.write(records.map((r) => JSON.stringify(r)).join('\n') + (records.length ? '\n' : ''));
process.stderr.write(`${JSON.stringify({ summary: reports, total: records.length })}\n`);
