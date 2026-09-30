#!/usr/bin/env node

import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';

const { parseSync } = createRequire(join(process.cwd(), 'package.json'))('oxc-parser');

const SOURCE_FILE = /\.(?:ts|tsx|mts|cts|js|jsx|mjs|cjs)$/;
const JAVASCRIPT_FILE = /\.(?:js|jsx|mjs|cjs)$/;
const READ_BY_A_TOOL =
  /[@#]__(?:PURE|NO_SIDE_EFFECTS)__|webpack[A-Z]\w*:|@vite-ignore|@jsx(?:ImportSource|Runtime|Frag)?\b|(?:istanbul|c8|v8) ignore|@(?:vitest|jest)-environment|(?:eslint|oxlint)-(?:disable|enable)|biome-ignore|prettier-ignore|jscpd:ignore|@ts-(?:ignore|expect-error|nocheck|check)|@(?:internal|public|beta|alpha|license|preserve)\b|^\/\*!|^\/\/\/ </;
const JSDOC_TYPE =
  /@(?:type|typedef|callback|template|satisfies|import|enum)\b|@(?:param|arg|argument|returns?|prop|property|this)\s*\{/;
const BARE_INTERNAL_TAG = '/** @internal */';
const POSITION_KEYS = new Set(['start', 'end', 'range', 'loc']);
const STATUS_NAMES = { A: 'added', D: 'deleted' };
const RESTORE =
  'The strip may only delete comments that no tool reads. Restore each listed change, then rerun.';

const baseRef = process.argv[2];
if (!baseRef) {
  console.error('Usage: verify-strip-js.mjs <base-ref>');
  process.exit(2);
}

const git = (...args) => execFileSync('git', args, { encoding: 'utf8', maxBuffer: 1 << 30 });

function compiledJsxText(value) {
  const lines = value.split(/\r\n|\n|\r/);
  const lastNonEmptyLine = lines.findLastIndex((line) => /[^ \t]/.test(line));
  return lines
    .map((line, index) => {
      const withoutLeading = index === 0 ? line.replaceAll('\t', ' ') : line.replaceAll('\t', ' ').replace(/^ +/, '');
      const trimmed = index === lines.length - 1 ? withoutLeading : withoutLeading.replace(/ +$/, '');
      return trimmed && index !== lastNonEmptyLine ? `${trimmed} ` : trimmed;
    })
    .join('');
}

function normalizeList(nodes) {
  const merged = [];
  for (const node of nodes) {
    if (node?.type === 'JSXExpressionContainer' && node.expression.type === 'JSXEmptyExpression') continue;
    const previous = merged.at(-1);
    if (previous?.type === 'JSXText' && node?.type === 'JSXText') {
      merged[merged.length - 1] = { type: 'JSXText', value: previous.value + node.value };
      continue;
    }
    merged.push(node);
  }
  return merged
    .map((node) => (node?.type === 'JSXText' ? { type: 'JSXText', value: compiledJsxText(node.value) } : node))
    .filter((node) => node?.type !== 'JSXText' || node.value !== '')
    .map(normalize);
}

function normalize(value) {
  if (Array.isArray(value)) return normalizeList(value);
  if (value === null || typeof value !== 'object') return value;
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => !POSITION_KEYS.has(key))
      .map(([key, child]) => [key, normalize(child)]),
  );
}

function commentsOf(text, parsed) {
  return parsed.comments.map((comment) => ({
    line: text.slice(0, comment.start).split('\n').length,
    column: comment.start - text.lastIndexOf('\n', comment.start - 1),
    text: text.slice(comment.start, comment.end),
  }));
}

const firstLine = (text) => text.split('\n')[0];
const plural = (count, noun) => `${count} ${noun}${count === 1 ? '' : 's'}`;

const diffFields = git('diff', '--name-status', '--no-renames', '--relative', '-z', baseRef).split('\0');
const changes = [
  ...Array.from({ length: Math.floor(diffFields.length / 2) }, (_, index) => ({
    status: diffFields[index * 2],
    path: diffFields[index * 2 + 1],
  })),
  ...git('ls-files', '-z', '--others', '--exclude-standard')
    .split('\0')
    .filter(Boolean)
    .map((path) => ({ status: 'A', path })),
].sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));

const failures = [];
let strippedFiles = 0;
let deletedComments = 0;
for (const { status, path } of changes) {
  if (status !== 'M') {
    failures.push(`${path}  ${STATUS_NAMES[status] ?? `status ${status}`}`);
    continue;
  }
  if (!SOURCE_FILE.test(path)) {
    failures.push(`${path}  changed, but it is not a source file`);
    continue;
  }
  const beforeText = git('show', `${baseRef}:./${path}`);
  const afterText = readFileSync(path, 'utf8');
  const before = parseSync(path, beforeText);
  const after = parseSync(path, afterText);
  const unparsable = [
    ['before', before],
    ['after', after],
  ].find(([, parsed]) => parsed.errors.length > 0);
  if (unparsable) {
    failures.push(`${path}  cannot be parsed ${unparsable[0]} the strip: ${unparsable[1].errors[0].message}`);
    continue;
  }
  if (JSON.stringify(normalize(before.program)) !== JSON.stringify(normalize(after.program))) {
    failures.push(`${path}  code changed`);
  }
  const remaining = commentsOf(afterText, after);
  const deleted = commentsOf(beforeText, before).filter((comment) => {
    const match = remaining.findIndex((kept) => kept.text === comment.text);
    if (match === -1) return true;
    remaining.splice(match, 1);
    return false;
  });
  const internalTagDeletions = deleted.filter((comment) => /@internal\b/.test(comment.text));
  const reducedToBareTag = new Set();
  const added = remaining.filter((comment) => {
    if (comment.text !== BARE_INTERNAL_TAG || internalTagDeletions.length === 0) return true;
    reducedToBareTag.add(internalTagDeletions.shift());
    return false;
  });
  const readByATool = (comment) =>
    !reducedToBareTag.has(comment) &&
    (READ_BY_A_TOOL.test(comment.text) || (JAVASCRIPT_FILE.test(path) && JSDOC_TYPE.test(comment.text)));
  failures.push(
    ...added.map((comment) => `${path}:${comment.line}:${comment.column}  comment added: ${firstLine(comment.text)}`),
    ...deleted
      .filter(readByATool)
      .map((comment) => `${path}:${comment.line}:${comment.column}  tool-read comment deleted: ${firstLine(comment.text)}`),
  );
  strippedFiles += 1;
  deletedComments += deleted.length;
}

if (failures.length > 0) {
  console.error([...failures, RESTORE].join('\n'));
  process.exit(1);
}
console.log(`Code unchanged: ${plural(deletedComments, 'comment')} deleted in ${plural(strippedFiles, 'file')}.`);
