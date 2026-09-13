package stltransport

import (
	"context"
	"github.com/gotd/td/telegram/dcs"
	"github.com/gotd/td/tg"
	"testing"
)

func TestSelectorRejectsUnadvertisedAndWrongDC(t *testing.T) {
	option := tg.DCOption{ID: 4, IPAddress: "149.154.167.255", Port: 443, MediaOnly: true}
	r := &Resolver{}
	list := dcs.List{Options: []tg.DCOption{option}}
	r.Select("4/media/127.0.0.1/443")
	if _, err := r.MediaOnly(context.Background(), 4, list); err == nil {
		t.Fatal("unadvertised endpoint accepted")
	}
	r.Select(Key(option))
	if _, err := r.MediaOnly(context.Background(), 2, list); err == nil {
		t.Fatal("wrong DC accepted")
	}
	option.CDN = true
	if Allowed(option) {
		t.Fatal("CDN endpoint accepted as regular media endpoint")
	}
}
