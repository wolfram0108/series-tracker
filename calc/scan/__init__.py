"""Pure count of the scan: what a run knows of the work of its executors, the changes of a series by the user a sync waits for, the state
of the scheduler and when the next pass is due, how a source stands after its table was asked, and what to do with a command a restart found.
The module `scan` asks the neighbours, holds the tasks and writes its table; this says what the answers and the states come to."""
