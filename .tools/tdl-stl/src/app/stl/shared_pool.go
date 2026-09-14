package stl

import (
	"context"
	"errors"
	"io"
	"net"
	"sync"

	"github.com/gotd/td/bin"
	"github.com/gotd/td/pool"
	"github.com/gotd/td/rpc"
	"github.com/gotd/td/telegram"
	"github.com/gotd/td/tg"
	"github.com/iyear/tdl/core/stltransport"
)

type poolIdentity struct {
	client   *telegram.Client
	dc       int
	endpoint string
}
type sharedPool struct {
	pool       telegram.CloseInvoker
	api        *tg.Client
	references int
}
type poolRegistry struct {
	mu      sync.Mutex
	entries map[poolIdentity]*sharedPool
}
type poolLease struct {
	registry *poolRegistry
	key      poolIdentity
	entry    *sharedPool
	once     sync.Once
}

var downloadPools poolRegistry

func connectionFailure(err error) bool {
	var network net.Error
	return errors.Is(err, pool.ErrConnDead) || errors.Is(err, rpc.ErrEngineClosed) ||
		errors.Is(err, context.Canceled) || errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) ||
		errors.Is(err, net.ErrClosed) || errors.As(err, &network)
}

func (p *poolLease) Invoke(ctx context.Context, in bin.Encoder, out bin.Decoder) error {
	err := p.entry.pool.Invoke(ctx, in, out)
	if ctx.Err() == nil && connectionFailure(err) {
		// Existing users retain their leases. A retry must open a fresh pool
		// instead of joining the same dead connections while peers unwind.
		p.registry.mu.Lock()
		if p.registry.entries[p.key] == p.entry {
			delete(p.registry.entries, p.key)
		}
		p.registry.mu.Unlock()
	}
	return err
}
func (p *poolLease) Close() error {
	var err error
	p.once.Do(func() {
		p.registry.mu.Lock()
		p.entry.references--
		last := p.entry.references == 0
		if last && p.registry.entries[p.key] == p.entry {
			delete(p.registry.entries, p.key)
		}
		p.registry.mu.Unlock()
		if last {
			err = p.entry.pool.Close()
		}
	})
	return err
}
func (r *poolRegistry) acquire(ctx context.Context, key poolIdentity, open func() (telegram.CloseInvoker, *tg.Client, error)) (*poolLease, *tg.Client, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return nil, nil, err
	}
	entry := r.entries[key]
	if entry == nil {
		pool, api, err := open()
		if err != nil {
			return nil, nil, err
		}
		entry = &sharedPool{pool: pool, api: api}
		if r.entries == nil {
			r.entries = map[poolIdentity]*sharedPool{}
		}
		r.entries[key] = entry
	}
	entry.references++
	return &poolLease{registry: r, key: key, entry: entry}, entry.api, nil
}
func poolClient(ctx context.Context, c *telegram.Client, r *stltransport.Resolver, dc int, key string) (telegram.CloseInvoker, *tg.Client, error) {
	lease, _, err := downloadPools.acquire(ctx, poolIdentity{c, dc, key}, func() (telegram.CloseInvoker, *tg.Client, error) { return openPoolClient(ctx, c, r, dc, key) })
	if err != nil {
		return nil, nil, err
	}
	return lease, poolAPI(ctx, lease), nil
}
