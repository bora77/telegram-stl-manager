package stl

import (
	"context"
	"fmt"
	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/message"
	"github.com/gotd/td/telegram/message/styling"
	"github.com/gotd/td/telegram/peers"
	"github.com/gotd/td/telegram/uploader"
	"github.com/iyear/tdl/core/storage"
	"github.com/iyear/tdl/core/util/tutil"
	"os"
	"path/filepath"
	"sync"
	"time"
)

type uploadProgress struct {
	mu       sync.Mutex
	path     string
	last     time.Time
	started  time.Time
	uploaded int64
}

func (p *uploadProgress) Chunk(ctx context.Context, s uploader.ProgressState) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if s.Uploaded < p.uploaded {
		return nil
	}
	p.uploaded = s.Uploaded
	if time.Since(p.last) < 200*time.Millisecond && s.Uploaded < s.Total {
		return nil
	}
	p.last = time.Now()
	return WriteJSON(p.path, map[string]any{"uploaded": s.Uploaded, "total": s.Total, "speed_mbps": float64(s.Uploaded) / time.Since(p.started).Seconds() / 1e6})
}
func SendReleaseFile(ctx context.Context, c *telegram.Client, kv storage.Storage, chat, path, caption, progress string, photo bool) error {
	if chat == "" || path == "" || progress == "" {
		return fmt.Errorf("missing release upload parameters")
	}
	manager := peers.Options{Storage: storage.NewPeers(kv)}.Build(c.API())
	peer, err := tutil.GetInputPeer(ctx, manager, chat)
	if err != nil {
		return err
	}
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return fmt.Errorf("upload input is not a regular file")
	}
	up := uploader.NewUploader(c.API()).WithThreads(4).WithPartSize(512 * 1024).WithProgress(&uploadProgress{path: progress, started: time.Now()})
	uploaded, err := up.Upload(ctx, uploader.NewUpload(filepath.Base(path), f, info.Size()))
	if err != nil {
		return err
	}
	sender := message.NewSender(c.API()).To(peer.InputPeer())
	if photo {
		_, err = sender.Media(ctx, message.UploadedPhoto(uploaded, styling.Plain(caption)))
	} else {
		_, err = sender.Media(ctx, message.UploadedDocument(uploaded, styling.Plain(caption)).Filename(filepath.Base(path)).MIME("application/x-7z-compressed"))
	}
	return err
}
