package service

import (
	"crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"time"
)

// NewWriteID is the key a write is stored once by: a UUIDv7, a 48-bit
// millisecond timestamp then randomness, so keys sort into the order things
// happened.
func NewWriteID(now time.Time) string {
	var id [16]byte
	var millis [8]byte
	binary.BigEndian.PutUint64(millis[:], uint64(now.UnixMilli()))
	copy(id[0:6], millis[2:8])
	_, _ = rand.Read(id[6:])
	id[6] = 0x70 | id[6]&0x0f
	id[8] = 0x80 | id[8]&0x3f
	text := hex.EncodeToString(id[:])
	return text[0:8] + "-" + text[8:12] + "-" + text[12:16] + "-" + text[16:20] + "-" + text[20:]
}
