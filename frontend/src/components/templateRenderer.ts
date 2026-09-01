/**
 * Client-side AI template rendering utilities.
 *
 * Extracted from AITemplatesPanel.tsx so the renderer is testable in isolation
 * and reusable wherever templates need to be previewed without a server round-trip.
 *
 * The interpolation rules match the backend's variable resolution (see
 * backend/api/ai/analyzer.py). Built-in variables are resolved from the call
 * site; all others are left as `{{name}}` placeholders.
 */

export interface RenderContext {
  symbol: string;
  timeframe: string;
  [key: string]: string;
}

export interface RenderResult {
  system_prompt_rendered: string;
  variables_used: Record<string, string>;
  missing_variables: string[];
}

/**
 * Parse a comma-separated variables string into an array of trimmed names.
 */
export function parseVariables(raw: string): string[] {
  return raw.split(',').map(v => v.trim()).filter(Boolean);
}

/**
 * Client-side template renderer.
 *
 * Replaces `{{name}}` tokens with values from `context`. Built-in variables
 * (`symbol`, `timeframe`) are always resolved from the context object;
 * unknown tokens are left as-is so the caller can highlight them as missing.
 *
 * @param template  Raw system prompt template string (may contain `{{var}}` tokens).
 * @param context   Variable name → value map for interpolation.
 * @returns         Rendered prompt plus metadata about which variables were used / missing.
 */
export function renderTemplate(template: string, context: RenderContext): RenderResult {
  const variables_used: Record<string, string> = {};
  const allVars: string[] = [];

  const rendered = template.replace(
    /\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g,
    (_match, name: string) => {
      allVars.push(name);
      const value = context[name];
      if (value !== undefined) {
        variables_used[name] = value;
        return value;
      }
      // Leave unknown tokens as-is so the UI can surface them as missing.
      return `{{${name}}}`;
    },
  );

  // missing = all vars present in the template but not in context
  const known = new Set(Object.keys(context));
  const missing_variables = allVars.filter(v => !known.has(v));

  return { system_prompt_rendered: rendered, variables_used, missing_variables };
}
