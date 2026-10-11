// Report editing uses a floating assistant so the document never loses width.
export function usesDockedAssistant(path: string): boolean {
  return path.startsWith("/tasks/");
}
