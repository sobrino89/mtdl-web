// The sections of .env.example: a comment line after a blank line starts one, "# NAME=value  # meaning" is a
// variable in it. Shared by the EnvTable component and check-env.mjs.
export function envSections(text) {
  const sections = new Map();
  let current = null;
  let afterBlank = false;
  for (const line of text.split('\n')) {
    const variable = line.match(/^# ([A-Z_]+)=("[^"]*"|\S*)\s*(?:#\s*(.*))?$/);
    if (variable) {
      if (current === null) throw new Error(`.env.example: ${variable[1]} stands outside a section`);
      current.push({ name: variable[1], fallback: variable[2].replace(/^"|"$/g, ''), meaning: variable[3] ?? '' });
    } else if (afterBlank && /^# \S/.test(line)) {
      current = [];
      sections.set(line.slice(2).trim(), current);
    }
    afterBlank = line.trim() === '';
  }
  return sections;
}
