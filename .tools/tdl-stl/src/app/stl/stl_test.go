package stl

import (
	"bytes"
	"github.com/gotd/td/tg"
	"os"
	"path/filepath"
	"testing"
)

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
