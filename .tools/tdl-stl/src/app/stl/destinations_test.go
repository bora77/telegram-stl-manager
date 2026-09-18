package stl

import (
	"github.com/gotd/td/tg"
	"testing"
)

func TestDestinationUploadPermissions(t *testing.T) {
	cases := []struct {
		name    string
		chat    tg.ChatClass
		allowed bool
		id      string
	}{
		{"private group owner", &tg.Chat{ID: 123, Title: "Pad", Creator: true}, true, "-123"},
		{"left group", &tg.Chat{ID: 123, Left: true}, false, ""},
		{"migrated group", &tg.Chat{ID: 123, MigratedTo: &tg.InputChannel{ChannelID: 456}}, false, ""},
		{"group files forbidden", &tg.Chat{ID: 123, DefaultBannedRights: tg.ChatBannedRights{SendDocs: true}}, false, ""},
		{"group admin override", &tg.Chat{ID: 123, AdminRights: tg.ChatAdminRights{DeleteMessages: true}, DefaultBannedRights: tg.ChatBannedRights{SendDocs: true}}, true, "-123"},
		{"broadcast subscriber", &tg.Channel{ID: 456, Broadcast: true}, false, ""},
		{"broadcast admin cannot post", &tg.Channel{ID: 456, Broadcast: true, AdminRights: tg.ChatAdminRights{DeleteMessages: true}}, false, ""},
		{"broadcast poster", &tg.Channel{ID: 456, Broadcast: true, AdminRights: tg.ChatAdminRights{PostMessages: true}}, true, "-1000000000456"},
		{"supergroup banned", &tg.Channel{ID: 456, Megagroup: true, BannedRights: tg.ChatBannedRights{SendMedia: true}}, false, ""},
		{"supergroup member", &tg.Channel{ID: 456, Megagroup: true}, true, "-1000000000456"},
		{"gigagroup member", &tg.Channel{ID: 456, Gigagroup: true}, false, ""},
		{"incomplete metadata", &tg.Channel{ID: 456, Min: true}, false, ""},
		{"unavailable", &tg.ChatForbidden{ID: 123}, false, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			d, ok := destination(c.chat)
			if ok != c.allowed || (ok && d.ID != c.id) {
				t.Fatalf("got %+v %v", d, ok)
			}
		})
	}
}
