package stl

import (
	"context"
	"fmt"
	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/peers"
	"github.com/gotd/td/tg"
	"github.com/iyear/tdl/core/storage"
	"github.com/iyear/tdl/core/util/tutil"
)

// Forward one explicitly selected pad message without sender attribution.
// Captions are retained. No completion callback is sent.
func ForwardReleaseMessage(ctx context.Context, c *telegram.Client, kv storage.Storage, from, to string, id int, random int64, output string) error {
	if from == "" || to == "" || id <= 0 || random == 0 || output == "" {
		return fmt.Errorf("missing forward parameters")
	}
	manager := peers.Options{Storage: storage.NewPeers(kv)}.Build(c.API())
	source, err := tutil.GetInputPeer(ctx, manager, from)
	if err != nil {
		return err
	}
	dest, err := tutil.GetInputPeer(ctx, manager, to)
	if err != nil {
		return err
	}
	bot, ok := dest.(peers.User)
	if !ok || !bot.Raw().Bot {
		return fmt.Errorf("release recipient must be a bot")
	}
	result, err := c.API().MessagesForwardMessages(ctx, &tg.MessagesForwardMessagesRequest{FromPeer: source.InputPeer(), ToPeer: dest.InputPeer(), ID: []int{id}, RandomID: []int64{random}, DropAuthor: true})
	if err != nil {
		return err
	}
	return WriteJSON(output, result)
}
