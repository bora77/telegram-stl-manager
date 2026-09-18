package stl

import (
	"context"
	"fmt"
	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/query/dialogs"
	"github.com/gotd/td/tg"
	"sort"
	"time"
)

type Destination struct {
	ID       string `json:"id"`
	Title    string `json:"title"`
	Kind     string `json:"kind"`
	Username string `json:"username,omitempty"`
}

func forbidsArchives(r tg.ChatBannedRights) bool {
	if r.UntilDate > 0 && int64(r.UntilDate) < time.Now().Unix() {
		return false
	}
	return r.ViewMessages || r.SendMessages || r.SendMedia || r.SendDocs
}

func destination(chat tg.ChatClass) (Destination, bool) {
	switch c := chat.(type) {
	case *tg.Chat:
		if c.Left || c.Deactivated || c.MigratedTo != nil {
			return Destination{}, false
		}
		if !c.Creator && c.AdminRights.Zero() && forbidsArchives(c.DefaultBannedRights) {
			return Destination{}, false
		}
		return Destination{ID: fmt.Sprint(-c.ID), Title: c.Title, Kind: "group"}, true
	case *tg.Channel:
		if c.Left || c.Min {
			return Destination{}, false
		}
		if c.Broadcast {
			if !c.Creator && !c.AdminRights.PostMessages {
				return Destination{}, false
			}
			return Destination{ID: fmt.Sprint(-1000000000000 - c.ID), Title: c.Title, Kind: "channel", Username: c.Username}, true
		}
		if c.Gigagroup && !c.Creator && c.AdminRights.Zero() {
			return Destination{}, false
		}
		if !c.Creator && c.AdminRights.Zero() && (forbidsArchives(c.BannedRights) || forbidsArchives(c.DefaultBannedRights)) {
			return Destination{}, false
		}
		return Destination{ID: fmt.Sprint(-1000000000000 - c.ID), Title: c.Title, Kind: "group", Username: c.Username}, true
	}
	return Destination{}, false
}

// Only dialog metadata is inspected. No history, forum topics, or attachments are fetched.
func Destinations(ctx context.Context, c *telegram.Client, output string) error {
	iter := dialogs.NewQueryBuilder(c.API()).GetDialogs().BatchSize(100).Iter()
	found := map[string]Destination{}
	for iter.Next(ctx) {
		e := iter.Value()
		var raw tg.ChatClass
		switch p := e.Peer.(type) {
		case *tg.InputPeerChat:
			if v, ok := e.Entities.Chat(p.ChatID); ok {
				raw = v
			}
		case *tg.InputPeerChannel:
			if v, ok := e.Entities.Channel(p.ChannelID); ok {
				raw = v
			}
		}
		if d, ok := destination(raw); ok {
			found[d.ID] = d
		}
	}
	if err := iter.Err(); err != nil {
		return fmt.Errorf("could not list Telegram destinations: %w", err)
	}
	result := make([]Destination, 0, len(found))
	for _, d := range found {
		result = append(result, d)
	}
	sort.Slice(result, func(i, j int) bool {
		if result[i].Title == result[j].Title {
			return result[i].ID < result[j].ID
		}
		return result[i].Title < result[j].Title
	})
	return WriteJSON(output, map[string]any{"destinations": result})
}
