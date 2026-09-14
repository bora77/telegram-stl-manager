package telegram

import "context"

// File pools share a DC's permanent key. Import authorization once for that
// key, rather than importing it again while other file pools are using it.
// The guard also prevents two first pools from initializing the DC together.
func (c *Client) authorizeDCOnce(ctx context.Context, dc int, authorize func() error) error {
	c.dcAuthorizationMu.Lock()
	defer c.dcAuthorizationMu.Unlock()
	if err := ctx.Err(); err != nil {
		return err
	}
	c.sessionsMux.Lock()
	session := c.sessions[dc]
	c.sessionsMux.Unlock()
	if session != nil {
		key := session.Load().AuthKey
		if previous, ok := c.dcAuthorizations[dc]; ok && !key.Zero() && previous == key.ID {
			return nil
		}
	}
	if err := authorize(); err != nil {
		return err
	}
	c.sessionsMux.Lock()
	session = c.sessions[dc]
	c.sessionsMux.Unlock()
	if session != nil {
		key := session.Load().AuthKey
		if !key.Zero() {
			if c.dcAuthorizations == nil {
				c.dcAuthorizations = map[int][8]byte{}
			}
			c.dcAuthorizations[dc] = key.ID
		}
	}
	return nil
}
