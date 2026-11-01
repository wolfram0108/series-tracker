// One order for every list the interface sorts by name: the default locale of the machine would order the same
// names differently on another one.
const collator = new Intl.Collator("ru")

export const compareNames = (a: string, b: string): number => collator.compare(a, b)
