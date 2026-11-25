package service

import (
	"context"
	"encoding/json"
	"net/url"
)

// Document is one of the service's documents, as its whole text.
type Document struct {
	Text string `json:"text"`
}

// documentPath is the route of the document called name.
func documentPath(name string) string { return "/documents/" + url.PathEscape(name) }

// Document reads the document called name. One never written is refused with 404.
func (c *Client) Document(ctx context.Context, name string) (Document, error) {
	var answer Document
	_, err := c.Get(ctx, documentPath(name), nil, &answer)
	return answer, err
}

// DocumentTemplate reads the annotated example that the document called name starts from.
func (c *Client) DocumentTemplate(ctx context.Context, name string) (Document, error) {
	var answer Document
	_, err := c.Get(ctx, documentPath(name)+"/template", nil, &answer)
	return answer, err
}

// SaveDocument replaces the document called name with text. The service
// validates the whole text and refuses it with 422, naming the first fault.
func (c *Client) SaveDocument(ctx context.Context, name, text string) error {
	return c.Put(ctx, documentPath(name), map[string]string{"text": text}, nil)
}

// SourcesContract reads the shape of a lane, as an annotated example. Every
// source's read is shaped into it.
func (c *Client) SourcesContract(ctx context.Context) (json.RawMessage, error) {
	var answer json.RawMessage
	_, err := c.Get(ctx, "/sources/contract", nil, &answer)
	return answer, err
}
