package cmd
import (
 "context"
 "testing"
 "time"
 "github.com/gotd/td/bin"
 "github.com/gotd/td/telegram"
 "github.com/gotd/td/tg"
)
func TestUploadChunksBypassMetadataRateLimit(t *testing.T) {
 ctx,cancel:=context.WithTimeout(context.Background(),200*time.Millisecond);defer cancel()
 calls:=0
 next:=telegram.InvokeFunc(func(context.Context,bin.Encoder,bin.Decoder)error{calls++;return nil})
 invoke:=releaseAwareLimiter().Handle(next)
 for i:=0;i<20;i++ {
  if err:=invoke(ctx,&tg.UploadSaveFilePartRequest{},nil);err!=nil {t.Fatal(err)}
  if err:=invoke(ctx,&tg.UploadSaveBigFilePartRequest{},nil);err!=nil {t.Fatal(err)}
 }
 if calls!=40 {t.Fatalf("got %d calls",calls)}
}
