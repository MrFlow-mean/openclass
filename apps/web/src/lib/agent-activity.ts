export function publicAgentActivityLabel(label: string): string {
  return label.replace(/^(?:Codex|OpenAI)\b/i, "OpenClass");
}
