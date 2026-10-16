/**
 * The few Node APIs vite.config.ts uses, declared by hand.
 *
 * `@types/node` would be the usual answer, but every dependency here is pinned
 * to "latest" with the lockfile deciding the actual versions - adding one
 * package means `npm install` re-resolving all of them, which is a much bigger
 * change than three signatures. Only what is used is declared, so a wrong one
 * still fails the typecheck.
 */
declare module "node:fs" {
  export function readFileSync(path: string, encoding: string): string;
}

declare module "node:path" {
  export function dirname(path: string): string;
  export function resolve(...segments: string[]): string;
}

declare module "node:url" {
  export function fileURLToPath(url: string): string;
}

/** `import.meta.url`, which the config uses to find the repository root. */
interface ImportMeta {
  readonly url: string;
}
