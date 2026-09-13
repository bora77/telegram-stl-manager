// Package stltransport confines manual selection to endpoints advertised by Telegram.
package stltransport

import (
	"context"
	"fmt"
	"net"
	"sync"

	"github.com/gotd/td/telegram/dcs"
	"github.com/gotd/td/tg"
	"github.com/gotd/td/transport"
)

type contextKey struct{}
type Resolver struct {
	Base     dcs.Resolver
	Dial     dcs.DialFunc
	mu       sync.RWMutex
	selected string
}

func With(ctx context.Context, resolver *Resolver) context.Context {
	return context.WithValue(ctx, contextKey{}, resolver)
}
func From(ctx context.Context) *Resolver { r, _ := ctx.Value(contextKey{}).(*Resolver); return r }
func Key(o tg.DCOption) string {
	kind := "regular"
	if o.MediaOnly {
		kind = "media"
	}
	return fmt.Sprintf("%d/%s/%s/%d", o.ID, kind, o.IPAddress, o.Port)
}
func Allowed(o tg.DCOption) bool {
	return !o.CDN && !o.TCPObfuscatedOnly && o.ID > 0 && net.ParseIP(o.IPAddress) != nil && o.Port > 0 && o.Port <= 65535
}
func (r *Resolver) Select(key string) { r.mu.Lock(); defer r.mu.Unlock(); r.selected = key }
func (r *Resolver) Selected(key string) *Resolver {
	return &Resolver{Base: r.Base, Dial: r.Dial, selected: key}
}
func (r *Resolver) Primary(ctx context.Context, dc int, list dcs.List) (transport.Conn, error) {
	return r.Base.Primary(ctx, dc, list)
}
func (r *Resolver) CDN(ctx context.Context, dc int, list dcs.List) (transport.Conn, error) {
	return r.Base.CDN(ctx, dc, list)
}
func (r *Resolver) MediaOnly(ctx context.Context, dc int, list dcs.List) (transport.Conn, error) {
	r.mu.RLock()
	selected := r.selected
	r.mu.RUnlock()
	for _, option := range list.Options {
		if option.ID != dc || !Allowed(option) || Key(option) != selected {
			continue
		}
		list.Options = []tg.DCOption{option}
		resolver := dcs.Plain(dcs.PlainOptions{Dial: r.Dial, PreferIPv6: option.Ipv6})
		if option.MediaOnly {
			return resolver.MediaOnly(ctx, dc, list)
		}
		return resolver.Primary(ctx, dc, list)
	}
	return nil, fmt.Errorf("selected download endpoint is no longer advertised for DC %d", dc)
}
