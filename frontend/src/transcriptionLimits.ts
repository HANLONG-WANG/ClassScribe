export function transcriptionMaxMinutes(provider: "local" | "azure_mai") {
  return provider === "azure_mai" ? 119 : 90;
}
