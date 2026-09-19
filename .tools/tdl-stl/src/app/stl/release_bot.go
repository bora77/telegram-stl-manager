package stl

import (
	"context"
	"encoding/base64"
	"fmt"
	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/message"
	"github.com/gotd/td/telegram/peers"
	"github.com/gotd/td/tg"
	"github.com/iyear/tdl/core/storage"
	"github.com/iyear/tdl/core/util/tutil"
)

// Explicit bot-only operations; workflows select callbacks from observed replies.
func ReleaseBot(ctx context.Context, c *telegram.Client, kv storage.Storage, chat, action, value string, id int, output string) error {
	manager := peers.Options{Storage: storage.NewPeers(kv)}.Build(c.API())
	peer, err := tutil.GetInputPeer(ctx, manager, chat)
	if err != nil {
		return err
	}
	bot, ok := peer.(peers.User)
	if !ok || !bot.Raw().Bot {
		return fmt.Errorf("release recipient must be a bot")
	}
	switch action {
	case "info":
		return WriteJSON(output, map[string]any{"id": bot.Raw().ID, "bot": true, "title": bot.Raw().FirstName})
	case "text":
		if value == "" || len(value) > 4096 {
			return fmt.Errorf("invalid bot message")
		}
		_, err = message.NewSender(c.API()).To(peer.InputPeer()).Text(ctx, value)
	case "callback":
		data, e := base64.StdEncoding.DecodeString(value)
		if e != nil || len(data) == 0 || len(data) > 64 || id <= 0 {
			return fmt.Errorf("invalid bot callback")
		}
		_, err = c.API().MessagesGetBotCallbackAnswer(ctx, &tg.MessagesGetBotCallbackAnswerRequest{Peer: peer.InputPeer(), MsgID: id, Data: data})
	default:
		return fmt.Errorf("unknown bot operation")
	}
	return err
}
