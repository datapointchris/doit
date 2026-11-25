package service

// Lane is one lane of the lane contract: ranked rows, or a grid of cells.
type Lane struct {
	Name  string `json:"name"`
	Title string `json:"title"`
	// Meta is what the heading says beside the title, such as a count, or "".
	Meta string `json:"meta"`
	// Status is "unavailable" where the lane could not be read, and Reason says why.
	Status string `json:"status"`
	// Alert is a lane that carries problems rather than work.
	Alert bool `json:"alert"`
	// Reason is null where the lane read whole, and says what it could not
	// read where it read in part.
	Reason string     `json:"reason"`
	Rows   []LaneRow  `json:"rows"`
	Grid   []GridCell `json:"grid"`
	// Total is how many rows the lane holds before any cut.
	Total int `json:"total"`
	// Hints are the commands that list the rest.
	Hints []string `json:"hints"`
}

// Available is whether the lane was read.
func (l Lane) Available() bool { return l.Status != "unavailable" }

// LaneRow is one thing to act on.
type LaneRow struct {
	Label string `json:"label"`
	Text  string `json:"text"`
	Note  string `json:"note"`
	// Urgency is none, due or overdue, and colors the note.
	Urgency string `json:"urgency"`
	// Handle is the command that acts on the row.
	Handle string `json:"handle"`
}

// GridCell is one member of a whole set, or one thing done.
type GridCell struct {
	Text string `json:"text"`
	Done bool   `json:"done"`
	// Handle is what to type to act on a cell not yet done, or "".
	Handle string `json:"handle"`
	// DoneAt is the instant it was done, or "" where its record kept only the day.
	DoneAt string `json:"done_at"`
	// DoneOn is the day it was done, or "" where its record kept none.
	DoneOn string `json:"done_on"`
	// ActorKind is the kind of actor the service names for the cell, as
	// printed, or "" where its record names nobody.
	ActorKind string `json:"actor_kind"`
}
