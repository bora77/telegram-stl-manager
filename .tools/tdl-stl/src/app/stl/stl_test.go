package stl

import (
	"bytes"
	"context"
	"fmt"
	"github.com/gotd/td/telegram/downloader"
	"github.com/gotd/td/tg"
	"github.com/iyear/tdl/core/tmedia"
	"os"
	"path/filepath"
	"testing"
	"time"
)

type strictFileClient struct {
	downloader.Client
	size      int64
	truncated bool
}

func (c strictFileClient) UploadGetFile(ctx context.Context, r *tg.UploadGetFileRequest) (tg.UploadFileClass, error) {
	if r.Offset >= c.size {
		return nil, fmt.Errorf("OFFSET_INVALID")
	}
	if r.Offset == 0 {
		time.Sleep(10 * time.Millisecond)
	} // EOF may arrive before an earlier chunk.
	n := min(int64(r.Limit), c.size-r.Offset)
	if c.truncated {
		n--
	}
	return &tg.UploadFile{Type: &tg.StorageFileUnknown{}, Bytes: bytes.Repeat([]byte{byte(r.Offset / partSize)}, int(n))}, nil
}

func TestParallelTransferNeverRequestsPastKnownSize(t *testing.T) {
	for _, size := range []int64{0, 17 * partSize, 17*partSize + 37} {
		file, err := os.CreateTemp(t.TempDir(), "payload")
		if err != nil {
			t.Fatal(err)
		}
		defer file.Close()
		writer := &countWriter{file: file, total: size}
		if err := transfer(context.Background(), strictFileClient{size: size}, &tmedia.Media{Size: size}, writer); err != nil {
			t.Fatal(err)
		}
		if writer.done != size {
			t.Fatalf("received %d of %d bytes", writer.done, size)
		}
		data, err := os.ReadFile(file.Name())
		if err != nil {
			t.Fatal(err)
		}
		if int64(len(data)) != size {
			t.Fatal("incomplete output")
		}
		for offset, value := range data {
			if value != byte(offset/partSize) {
				t.Fatalf("missing or wrong data at %d", offset)
			}
		}
	}
	// Check the actual 4.19 GB boundary without allocating a multi-GB fixture.
	size := int64(4000) * partSize
	client := sizedClient{Client: strictFileClient{size: size}, size: size}
	for _, offset := range []int64{size - partSize, size, size + partSize} {
		if _, err := client.UploadGetFile(context.Background(), &tg.UploadGetFileRequest{Offset: offset, Limit: partSize}); err != nil {
			t.Fatal(err)
		}
	}
}

func TestPrematureShortChunkIsNotAcceptedAsEOF(t *testing.T) {
	size := int64(partSize)
	if err := transfer(context.Background(), strictFileClient{size: size, truncated: true}, &tmedia.Media{Size: size}, &countWriter{total: size}); err == nil {
		t.Fatal("truncated attachment accepted")
	}
}

func fixture() (*tg.Message, Options) {
	o := Options{ChatID: 123456789, Topic: 200, Message: 100000, DocumentID: 42, Size: 10, DC: 4, Filename: "Artist 2026-09.zip"}
	message := &tg.Message{ID: o.Message, PeerID: &tg.PeerChannel{ChannelID: o.ChatID}}
	reply := &tg.MessageReplyHeader{ReplyToMsgID: o.Topic}
	reply.SetForumTopic(true)
	message.SetReplyTo(reply)
	message.SetMedia(&tg.MessageMediaDocument{Document: &tg.Document{ID: o.DocumentID, DCID: o.DC, Size: o.Size, Attributes: []tg.DocumentAttributeClass{&tg.DocumentAttributeFilename{FileName: o.Filename}}}})
	return message, o
}
func TestAttachmentRevalidatedBeforeDownload(t *testing.T) {
	m, o := fixture()
	if _, err := verifyMessage(m, o); err != nil {
		t.Fatal(err)
	}
	tests := []func(*tg.Message, *Options){
		func(m *tg.Message, o *Options) { o.ChatID = 0 },
		func(m *tg.Message, o *Options) { m.PeerID = &tg.PeerChannel{ChannelID: 123} },
		func(m *tg.Message, o *Options) { m.ReplyTo = nil },
		func(m *tg.Message, o *Options) { m.ReplyTo.(*tg.MessageReplyHeader).ReplyToMsgID = 123 },
		func(m *tg.Message, o *Options) { o.DocumentID++ },
		func(m *tg.Message, o *Options) { o.Size++ },
		func(m *tg.Message, o *Options) { o.Filename = "another.zip" },
		func(m *tg.Message, o *Options) { o.DC++ },
	}
	for i, mutate := range tests {
		m, o := fixture()
		mutate(m, &o)
		if _, err := verifyMessage(m, o); err == nil {
			t.Fatalf("unsafe case %d accepted", i)
		}
	}
}

func TestPrivateSourceMustBeConfigured(t *testing.T) {
	path := filepath.Join(t.TempDir(), "source.json")
	if _, err := LoadSource(path); err == nil {
		t.Fatal("missing source accepted")
	}
	for _, data := range []string{`{}`, `{"chat_id":0,"toc_message_id":100}`, `{"chat_id":123456789,"toc_message_id":-1}`, `not json`} {
		if err := os.WriteFile(path, []byte(data), 0600); err != nil {
			t.Fatal(err)
		}
		if _, err := LoadSource(path); err == nil {
			t.Fatal("invalid source accepted")
		}
	}
	if err := os.WriteFile(path, []byte(`{"chat_id":123456789,"toc_message_id":100}`), 0600); err != nil {
		t.Fatal(err)
	}
	source, err := LoadSource(path)
	if err != nil || source.ChatID != 123456789 {
		t.Fatal("valid local source rejected")
	}
}
func TestExactReceivedCountIsIndependentOfSparseOffset(t *testing.T) {
	writer := &countWriter{total: 100}
	if _, err := writer.WriteAt(bytes.Repeat([]byte{1}, 10), 80); err != nil {
		t.Fatal(err)
	}
	if writer.done != 10 {
		t.Fatalf("count=%d; sparse length was mistaken for received bytes", writer.done)
	}
	if _, err := writer.WriteAt(make([]byte, 10), 95); err == nil {
		t.Fatal("out-of-bounds data accepted")
	}
}
