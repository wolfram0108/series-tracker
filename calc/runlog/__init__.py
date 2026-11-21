"""Pure count of the dump of a run: what an entry of the dump is as a line of the file, how a payload is told without its secrets, which
dumps stay and which go, which event opens or closes a run. The module `runlog` takes the entries off the bus and the log and writes the files
(the disk, the threads); this says what to write and what to remove."""
