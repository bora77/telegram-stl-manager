// Package stlservice multiplexes independent jobs over one authenticated client.
package stlservice

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"sync"
	"time"
)

const Protocol = 1

type Request struct {
	Source string   `json:"source"`
	Args   []string `json:"args"`
}
type Response struct {
	OK       bool   `json:"ok"`
	Error    string `json:"error,omitempty"`
	Protocol int    `json:"protocol"`
	Source   string `json:"source"`
}
type Handler func(context.Context, Request) error

// Call never retries an accepted request: a lost response may follow a write.
func Call(ctx context.Context, path string, request Request) (bool, error) {
	conn, err := (&net.Dialer{Timeout: time.Second}).DialContext(ctx, "unix", path)
	if err != nil {
		return false, err
	}
	defer conn.Close()
	stop := context.AfterFunc(ctx, func() {
		// Keep the read half open until the cancelled job has closed its files.
		if unix, ok := conn.(*net.UnixConn); ok {
			unix.CloseWrite()
			conn.SetReadDeadline(time.Now().Add(4 * time.Second))
		} else {
			conn.Close()
		}
	})
	defer stop()
	if err = json.NewEncoder(conn).Encode(request); err != nil {
		return true, err
	}
	var response Response
	if err = json.NewDecoder(io.LimitReader(conn, 1024*1024)).Decode(&response); err != nil {
		return true, err
	}
	if err = ctx.Err(); err != nil {
		return true, err
	}
	if response.Protocol != Protocol || response.Source != request.Source {
		return true, errors.New("Telegram service identity changed")
	}
	if !response.OK {
		return true, fmt.Errorf("%s", response.Error)
	}
	return true, nil
}

func serveConn(ctx context.Context, conn net.Conn, source string, run Handler) {
	defer conn.Close()
	requestCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	stop := context.AfterFunc(ctx, func() { conn.Close() })
	defer stop()
	conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	line, err := bufio.NewReader(io.LimitReader(conn, 1024*1024)).ReadBytes('\n')
	if err != nil {
		return
	}
	conn.SetReadDeadline(time.Time{})
	var request Request
	response := Response{Protocol: Protocol, Source: source}
	if err = json.Unmarshal(line, &request); err == nil {
		if request.Source != source {
			err = errors.New("Telegram service source does not match this application")
		} else if len(request.Args) == 1 && request.Args[0] == "health" {
			response.OK = true
		} else {
			// Disconnect cancels only this job, never the shared client or its peers.
			go func() { io.Copy(io.Discard, conn); cancel() }()
			err = run(requestCtx, request)
			response.OK = err == nil
		}
	}
	if err != nil {
		response.Error = err.Error()
	}
	json.NewEncoder(conn).Encode(response)
}

func Serve(ctx context.Context, path, source string, idle time.Duration, run Handler) error {
	// The caller serializes startup; never unlink a live or non-socket path.
	if info, err := os.Lstat(path); err == nil {
		if info.Mode()&os.ModeSocket == 0 {
			return errors.New("Telegram service socket path is occupied")
		}
		if conn, err := net.DialTimeout("unix", path, time.Second); err == nil {
			conn.Close()
			return errors.New("Telegram service is already running")
		}
		if err = os.Remove(path); err != nil {
			return err
		}
	}
	listener, err := net.Listen("unix", path)
	if err != nil {
		return err
	}
	defer listener.Close()
	if err = os.Chmod(path, 0600); err != nil {
		return err
	}
	var mu sync.Mutex
	active := 0
	last := time.Now()
	var jobs sync.WaitGroup
	serviceCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	done := make(chan struct{})
	defer close(done)
	go func() {
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-done:
				return
			case <-ctx.Done():
				listener.Close()
				return
			case <-ticker.C:
				mu.Lock()
				expired := active == 0 && time.Since(last) >= idle
				mu.Unlock()
				if expired {
					listener.Close()
					return
				}
			}
		}
	}()
	for {
		conn, err := listener.Accept()
		if err != nil {
			// A connection accepted just as idle shutdown begins still gets to
			// finish. External shutdown and listener errors cancel all jobs.
			if ctx.Err() != nil || !errors.Is(err, net.ErrClosed) {
				cancel()
			}
			jobs.Wait()
			if errors.Is(err, net.ErrClosed) {
				return nil
			}
			return err
		}
		mu.Lock()
		active++
		mu.Unlock()
		jobs.Add(1)
		go func() {
			defer jobs.Done()
			defer func() { mu.Lock(); active--; last = time.Now(); mu.Unlock() }()
			serveConn(serviceCtx, conn, source, run)
		}()
	}
}
