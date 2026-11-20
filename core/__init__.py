"""The infrastructure of the process. Every name is imported from the file that defines it (core.bus, core.module,
core.runner, ...): the package re-exports nothing, so a leaf of it (core.contracts, core.reasons) can be imported without
loading the bus and the module wrapper."""
